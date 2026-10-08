"""
Verifikasi Kelengkapan Syarat Bayar - PLN
Auto-detect berdasarkan isi dokumen. PDF scan ditandai untuk verifikasi manual.

Cara pakai:
  python verifikasi_syarat_bayar_v2.py                     # tanya folder interaktif
  python verifikasi_syarat_bayar_v2.py "D:/folder/spk"     # langsung proses
"""

import os
import re
import sys
import io
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import logging
import pdfplumber    # ekstrak teks digital (non-scan)

# pdfminer (mesin di balik pdfplumber) memuntahkan WARNING per-font untuk PDF
# yang FontDescriptor-nya tanpa /FontBBox — umum di PDF hasil scanner/vendor.
# pdfminer sudah fallback ke bbox (0,0,0,0) dan ekstraksi teks tetap utuh, jadi
# warning ini cuma noise yang menutupi progres. ERROR ke atas tetap tampil.
logging.getLogger("pdfminer").setLevel(logging.ERROR)

sys.stdout.reconfigure(encoding="utf-8")

# ── OCR (Tesseract) untuk PDF scan ──────────────────────────────────────────
# Diproses lokal, gratis, offline. Aktif otomatis untuk PDF tanpa teks digital.
# Hasil OCR di-cache per folder (.ocr_cache) supaya run berikutnya instan.
TESSERACT_CMD = os.environ.get("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
TESSDATA_DIR  = os.path.join(os.environ.get("LOCALAPPDATA", ""), "tessdata")
OCR_LANG      = "ind"            # bahasa Indonesia
OCR_DPI       = 300             # resolusi render halaman; naikkan kalau scan kecil/buram
OCR_MAX_PX    = 3508            # sisi terpanjang render maks (= A4 @300 dpi), lihat _render_ocr
# --psm 1: segmentasi otomatis + deteksi orientasi -> scan yang tersimpan miring
# 90°/180° TANPA flag /Rotate tetap terbaca (kasus 0066 Faktur: conf 28 -> 76).
# Filter gambar (flatten/deskew/autocontrast/Sauvola) sudah diukur: tidak menambah
# akurasi pada scan sampel, jadi tidak dipakai.
OCR_CONFIG    = "--psm 1"
OCR_CACHE_DIR = ".ocr_cache"    # subfolder cache di dalam folder dokumen
OCR_WORKERS   = max(1, os.cpu_count() or 4)   # OCR halaman paralel (1 tesseract/CPU)

try:
    import fitz                 # PyMuPDF: render halaman PDF -> gambar (tanpa Poppler)
    import pytesseract
    from PIL import Image

    if os.path.isfile(TESSERACT_CMD):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    if os.path.isdir(TESSDATA_DIR):
        os.environ["TESSDATA_PREFIX"] = TESSDATA_DIR
    # Tiap proses tesseract single-thread; paralelisme datang dari OCR banyak
    # halaman sekaligus (ThreadPoolExecutor) -> 1 CPU per halaman, tanpa rebutan.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")

    # Versi engine masuk ke stamp cache: hasil OCR beda antar versi Tesseract,
    # jadi upgrade engine harus otomatis membatalkan .ocr_cache lama.
    OCR_VERSI    = str(pytesseract.get_tesseract_version())   # error kalau tesseract tak terpasang
    OCR_TERSEDIA = True
    OCR_ERROR    = ""
except Exception as _e:
    OCR_VERSI    = "none"
    OCR_TERSEDIA = False
    OCR_ERROR    = str(_e)

# ── Warna terminal ──────────────────────────────────────────────────────────
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

# ── Definisi syarat bayar ───────────────────────────────────────────────────
SYARAT = [
    {
        "kode": "a",
        "syarat": "Surat Permohonan Pembayaran",
        "pola_teks": [
            ["surat permohonan pembayaran"],
            ["permohonan pembayaran"],
        ],
        "nama_hint": ["permohonan bayar", "permohonan pembayaran"],
        "catatan": "",
    },
    {
        "kode": "b",
        "syarat": "Kuitansi / Invoice (4 rangkap, 1 asli bermaterai)",
        "pola_teks": [
            ["kuitansi"],
            ["kwitansi"],
            ["invoice", "pembayaran"],
        ],
        "nama_hint": ["kuitansi", "kwitansi", "invoice"],
        "catatan": "Pastikan fisik 4 rangkap, 1 asli bermaterai",
    },
    {
        "kode": "c",
        "syarat": "Faktur Pajak PPN (1 set)",
        "pola_teks": [
            ["faktur pajak"],
        ],
        "nama_hint": ["faktur"],
        "catatan": "",
    },
    {
        "kode": "d",
        "syarat": "Salinan NPWP",
        "pola_teks": [
            ["nomor pokok wajib pajak"],   # khas kartu NPWP; tak muncul di faktur pajak
        ],
        "nama_hint": ["npwp"],
        "catatan": "",
    },
    {
        "kode": "e",
        "syarat": "Salinan SK Pengukuhan PKP (SP.PKP)",
        "pola_teks": [
            ["pengukuhan pengusaha kena pajak"],
            ["surat pengukuhan pkp"],
            ["pengukuhan", "pkp"],
            ["kantor pelayanan pajak"],
        ],
        "nama_hint": ["pkp", "npwp"],
        "catatan": "Cek halaman PKP dalam file NPWP/PKP",
    },
    {
        "kode": "f",
        "syarat": "BAPP – kemajuan pekerjaan mencapai 100%",
        "pola_teks": [
            ["berita acara pemeriksaan pekerjaan", "100%"],
            ["pemeriksaan pekerjaan", "100%"],
            ["bapp", "100%"],
            ["kemajuan", "100%"],
        ],
        "nama_hint": ["bapp"],
        "catatan": "Harus menyatakan progress 100%",
    },
    {
        "kode": "g",
        "syarat": "Berita Acara Serah Terima Barang (BASTB) di gudang",
        "pola_teks": [
            # BASTB = frasa utuh "berita acara serah terima barang", jangan dipecah.
            # "serah terima barang" saja BUKAN BASTB.
            ["berita acara serah terima barang"],
            ["bastb"],
            ["berita acara", "serah terima barang"],
        ],
        "nama_hint": ["bastb", "barang"],
        "catatan": "Pastikan disebutkan 'di gudang unit pelaksana'",
    },
    {
        "kode": "h",
        "syarat": "Berita Acara Serah Terima Pertama (BASTP)",
        "pola_teks": [
            ["serah terima pekerjaan"],
            ["bastp"],
            ["berita acara serah terima"],
        ],
        "nama_hint": ["bastp"],
        "catatan": "",
    },
    {
        "kode": "i",
        "syarat": "Laporan Pertanggung Jawaban Material (LPJM)",
        "pola_teks": [
            ["lpjm"],
            ["laporan pertanggung jawaban material"],
            ["pertanggung jawaban material"],
        ],
        # PJM.pdf = LPJM; KHS = Kurva Harga Satuan (dokumen berbeda)
        "nama_hint": ["lpjm", "pjm"],
        "catatan": "File biasanya bernama PJM atau LPJM",
    },
    {
        "kode": "j",
        "syarat": "Foto-foto dokumentasi tahapan pekerjaan",
        "pola_teks": [
            ["dokumentasi pekerjaan"],
            ["foto dokumentasi"],
            ["foto dukumentasi"],
            ["dokumentasi", "pengukuran"],
        ],
        "nama_hint": ["dokumentasi", "foto"],
        "catatan": "",
    },
    {
        "kode": "k",
        "syarat": "Gambar Revisi / As Built Drawing",
        "pola_teks": [
            ["as built"],
            ["as build"],
            ["gambar revisi"],
            ["asbuild"],
        ],
        "nama_hint": ["asbuild", "as built", "as build", "gambar revisi"],
        "catatan": "",
    },
    {
        "kode": "l",
        "syarat": "Final Evaluasi CSMS",
        "pola_teks": [
            ["csms"],
            ["contractor safety management"],
            ["safety management system"],
        ],
        "nama_hint": ["csms", "evaluasi vendor"],
        "catatan": "",
    },
    {
        "kode": "m",
        "syarat": "Rapor Penilaian Kinerja Vendor",
        "pola_teks": [
            ["formulir rapot vendor"],
            ["formulir rapor vendor"],
            ["formulir raport vendor"],
            ["rapot vendor"],
            ["raport vendor"],
            ["rapor vendor"],
        ],
        "nama_hint": ["raport", "rapot", "rapor"],
        "catatan": "",
    },
    {
        "kode": "n",
        "syarat": "Surat Pernyataan Jaminan Pemeliharaan",
        "pola_teks": [
            ["surat pernyataan jaminan pemeliharaan"],
            ["surat jaminan pemeliharaan"],
            ["jaminan pemeliharaan"],
            ["surat pernyataan jaminan"],
            # varian vendor tanpa kata "jaminan" (kasus 0020.Pj):
            # judul "SURAT PERNYATAAN PEMELIHARAAN", isi "siap menjamin pemeliharaan"
            ["surat pernyataan pemeliharaan"],
            ["menjamin pemeliharaan"],
        ],
        "nama_hint": ["jaminan pemeliharaan", "jaminan", "surat jaminan", "garansi"],
        "catatan": "Surat pernyataan/jaminan untuk masa pemeliharaan pekerjaan",
    },
    {
        "kode": "o",
        "syarat": "Bukti setoran BPJS Ketenagakerjaan (JKK & JKM)",
        "pola_teks": [
            ["bpjs ketenagakerjaan"],
            ["iuran jasa konstruksi"],
            ["bpjs", "konstruksi"],
            ["jaminan kecelakaan kerja"],
        ],
        "nama_hint": ["bpjs"],
        "catatan": "Program JKK & JKM untuk tenaga kerja teknis",
    },
    {
        "kode": "p",
        "syarat": "Surat Pernyataan Kebenaran Pemasangan / Penggunaan Tanah Gardu",
        "pola_teks": [
            # judul dokumen asli (tanpa kata "format"); template kosong di kontrak
            # diawali "format surat pernyataan ..." sehingga otomatis diabaikan.
            ["surat pernyataan kesepakatan penggunaan tanah"],
            ["pernyataan kebenaran pemasangan"],
            ["kebenaran pemasangan"],
        ],
        "nama_hint": ["penggunaan tanah", "penggunaan lahan"],
        "catatan": "Khusus pekerjaan yang ada konstruksi gardu baru",
    },
    {
        "kode": "q",
        "syarat": "Dokumen RLB / NIDI",
        "pola_teks": [
            ["nomor identitas instalasi tenaga listrik"],
            ["nidi"],
            ["rlb"],
            ["rekomendasi laik bertegangan"],
        ],
        "nama_hint": ["nidi", "rlb"],
        "catatan": "Bisa RLB atau NIDI yang sudah terbit",
    },
    {
        "kode": "z",
        "syarat": "Kontrak Rinci (KR) / KHS (Kontrak Harga Satuan)",
        # Dokumen acuan: dideteksi dari NAMA FILE saja, isi TIDAK di-OCR.
        "pola_teks": [],
        "nama_hint": ["kontrak rinci", "kr", "khs", "kontrak harga satuan"],
        "izinkan_acuan": True,   # dokumen acuan boleh memenuhi syarat ini
        "catatan": "Dideteksi dari nama file (dokumen acuan kontrak); isi tidak di-OCR",
    },
]

# ── Kelengkapan Diluar Syarat Bayar SPMK ───────────────────────────────────
DILUAR_SYARAT = [
    {
        "kode": "r",
        "syarat": "Bobot Fisik",
        "pola_teks": [
            ["bobot fisik"],
            ["bobot kemajuan"],
            ["bobot", "pekerjaan", "%"],
        ],
        "nama_hint": ["bobot fisik", "bobot"],
        "catatan": "",
    },
    {
        "kode": "s",
        "syarat": "Evaluasi (pekerjaan tambah/kurang pada rincian)",
        "pola_teks": [
            # kolom tabel EVALUASI (VOL/Rp tambah-kurang) di rincian harga digital
            ["evaluasi", "tambah", "kurang"],
            # narasi addendum/MoM: "pekerjaan tambah/kurang ..." (scan/OCR)
            ["tambah/kurang"],
            ["rab evaluasi"],
            ["formulir evaluasi"],
            ["evaluasi pekerjaan"],
            ["evaluasi vendor"],
        ],
        "nama_hint": ["evaluasi"],
        "catatan": "Kolom evaluasi tambah/kurang di Rincian Harga & Volume",
    },
    {
        "kode": "t",
        "syarat": "ADD (Addendum)",
        # Dideteksi dari NAMA FILE + cek khusus halaman addendum di dalam bundel
        # KR (deteksi_addendum_dalam_kr). Hampir semua BA/permohonan MENYEBUT
        # nomor addendum ("nomor amandemen kontrak rinci : 0033.add/...") sebagai
        # referensi, jadi kata kunci di isi tak bisa membedakan sebutan vs
        # dokumen addendum asli (false-positive, kasus 0020.Pj).
        "pola_teks": [],
        "nama_hint": ["add", "addendum", "adendum", "amandemen"],
        # nama file acuan (mis. "ADD KHS") = addendum level KHS, bukan
        # deliverable addendum SPK ini — jangan dihitung ADA.
        "nama_abaikan_acuan": True,
        "catatan": "Lampirkan seluruh addendum yang diterbitkan",
    },
    {
        "kode": "u",
        "syarat": "MoM (Minutes of Meeting)",
        "pola_teks": [
            ["minutes of meeting"],
            ["risalah rapat"],
            ["notulen rapat"],
            ["mom"],
        ],
        "nama_hint": ["mom", "minutes", "notulen", "risalah"],
        "catatan": "",
    },
    {
        "kode": "v",
        "syarat": "BA Mapping / Tikor",
        "pola_teks": [
            ["berita acara", "mapping"],
            ["berita acara", "tikor"],
            ["ba mapping"],
            ["ba tikor"],
            ["tim koordinasi"],
        ],
        "nama_hint": ["mapping", "tikor", "ba mapping", "ba tikor"],
        "catatan": "",
    },
    {
        "kode": "w",
        "syarat": "BA IPMS",
        "pola_teks": [
            ["berita acara", "ipms"],
            ["ba ipms"],
            ["ipms"],
            ["integrated project management"],
        ],
        "nama_hint": ["ipms", "ba ipms"],
        "catatan": "",
    },
    {
        "kode": "x",
        "syarat": "SBUJK (Sertifikat Badan Usaha Jasa Konstruksi)",
        "pola_teks": [
            ["sertifikat badan usaha jasa konstruksi"],
            ["sbujk"],
            ["badan usaha jasa konstruksi"],
            ["sertifikat badan usaha"],   # kop sertifikat (hasil OCR scan)
        ],
        "nama_hint": ["sbujk"],
        "catatan": "Pastikan masih berlaku dan sesuai klasifikasi",
    },
    {
        "kode": "y",
        "syarat": "SPMK (Surat Perintah Mulai Kerja)",
        "pola_teks": [
            ["surat perintah mulai kerja"],
            ["spmk"],
            ["perintah mulai kerja"],
        ],
        "nama_hint": ["spmk", "perintah mulai kerja"],
        "catatan": "",
    },
]


# ── Pilih folder ────────────────────────────────────────────────────────────

def pilih_folder() -> str:
    print("\n" + "=" * 70)
    print("  VERIFIKASI KELENGKAPAN SYARAT BAYAR - PLN")
    print("  (Auto-detect isi dokumen | PDF scan dibaca via OCR Tesseract offline)")
    print("=" * 70 + "\n")

    if len(sys.argv) > 1:
        folder = " ".join(sys.argv[1:]).strip().strip('"').strip("'")
        print(f"  Folder dari argumen: {folder}")
    else:
        print("  Masukkan path folder dokumen tagihan yang akan dicek.")
        print(r"  Contoh: D:\PLN\2026\Tagihan\0057.SPK")
        print()
        folder = input("  >> Folder: ").strip().strip('"').strip("'")

    if not folder:
        print("\n  [ERROR] Folder tidak boleh kosong.")
        sys.exit(1)
    if not os.path.isdir(folder):
        print(f"\n  [ERROR] Folder tidak ditemukan: {folder}")
        sys.exit(1)

    jumlah_pdf = len([f for f in os.listdir(folder) if f.endswith(".pdf")])
    print(f"\n  Folder  : {folder}")
    print(f"  PDF ada : {jumlah_pdf} file")

    if len(sys.argv) <= 1:
        konfirmasi = input("\n  Lanjutkan verifikasi? (y/n) [default: y]: ").strip().lower()
        if konfirmasi == "n":
            print("\n  Dibatalkan.\n")
            sys.exit(0)

    print()
    return folder


# ── OCR PDF scan ────────────────────────────────────────────────────────────

def _render_ocr(pg) -> "Image.Image":
    """
    Render halaman PDF -> gambar grayscale untuk OCR.
    Sebagian scanner menyimpan foto 72 dpi dengan ukuran halaman raksasa
    (mis. 2925x2100 pt); di 300 dpi jadi gambar ~12000 px — lambat dan OCR malah
    lebih buruk. DPI diturunkan agar sisi terpanjang <= OCR_MAX_PX; halaman
    ukuran kertas biasa (A4/Letter) tetap 300 dpi.
    """
    dpi = min(OCR_DPI, round(OCR_MAX_PX * 72 / max(pg.rect.width, pg.rect.height)))
    pix = pg.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def ocr_pdf(path: str, folder: str, nama: str) -> str:
    """
    OCR seluruh halaman PDF -> teks (lowercase) memakai Tesseract.
    Hasil disimpan di cache (.ocr_cache) dan dipakai ulang selama file tak berubah.
    File PDF asli tidak diubah.
    """
    cache_dir  = os.path.join(folder, OCR_CACHE_DIR)
    cache_file = os.path.join(cache_dir, nama + ".txt")
    stat       = os.stat(path)
    stamp      = (f"# stamp:{stat.st_size}:{int(stat.st_mtime)}:dpi{OCR_DPI}:max{OCR_MAX_PX}:"
                  f"{OCR_CONFIG}:{OCR_LANG}:v{OCR_VERSI}:pg1")

    # pakai cache jika stamp cocok
    if os.path.isfile(cache_file):
        try:
            with open(cache_file, encoding="utf-8") as f:
                isi = f.read()
            baris1, _, sisa = isi.partition("\n")
            if baris1 == stamp:
                return sisa, True   # (teks, dari_cache)
        except Exception:
            pass

    # Render tiap halaman -> gambar grayscale (langsung dari pixmap, tanpa PNG).
    # Grayscale + tanpa encode/decode PNG = jauh lebih ringan; Tesseract toh
    # membinarisasi gambar, jadi akurasi tak berubah.
    doc = fitz.open(path)
    try:
        imgs = [_render_ocr(pg) for pg in doc]
    finally:
        doc.close()

    def _ocr_satu(img):
        return pytesseract.image_to_string(img, lang=OCR_LANG, config=OCR_CONFIG).lower()

    # OCR halaman secara paralel (1 tesseract single-thread per CPU). Urutan
    # halaman dijaga oleh executor.map.
    if len(imgs) > 1 and OCR_WORKERS > 1:
        with ThreadPoolExecutor(max_workers=min(OCR_WORKERS, len(imgs))) as ex:
            hasil_hal = list(ex.map(_ocr_satu, imgs))
    else:
        hasil_hal = [_ocr_satu(img) for img in imgs]
    teks = "\f".join(h + "\n" for h in hasil_hal)   # \f = pembatas halaman

    # simpan cache
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(stamp + "\n" + teks)
    except Exception:
        pass

    return teks, False


# ── Buang pasal "cara pembayaran" (daftar syarat di dalam kontrak) ───────────
# Kontrak Rinci/KHS/SPMK memuat pasal "cara pembayaran" yang menyebut SELURUH
# syarat bayar (surat permohonan, kuitansi, bapp, jaminan pemeliharaan, dst).
# Daftar ini bukan deliverable — kalau ikut dicocokkan, kontrak ke-detect sebagai
# semua syarat (false-positive). Blok ini dibuang sebelum pencocokan.
# Catatan format (dikonfirmasi user): di kontrak, info transfer/rekening SELALU
# muncul tepat setelah daftar dokumen → dipakai sebagai penanda akhir blok.

_RE_AWAL_BAYAR  = re.compile(
    r"(tata\s+cara\s+pembayaran|cara\s+pembayaran|"
    r"syarat[-\s]*syarat\s+pembayaran|syarat\s+pembayaran|"
    r"terlampir\s+kami\s+sampaikan)"
)
_RE_DAFTAR      = re.compile(r"(melampirkan|sebagai\s+berikut|dilampirkan)")
_RE_AKHIR_BAYAR = re.compile(
    r"(pemindahbukuan|transfer\s+ke|nomor\s+rekening|no\.?\s*rekening)"
)
# Akhir daftar lampiran surat permohonan: kalimat "... maka cukup melampirkan
# dokumen nidi ...". Kalimatnya IKUT dibuang sampai akhir baris, karena "nidi"
# sendiri kata kunci syarat (q) — kalau ditinggal jadi false positive baru.
_RE_AKHIR_NIDI  = re.compile(
    r"maka\s+cukup\s+melampirkan\s+dokumen\s+nidi[^\n]*\n?"
)
_MAKS_BLOK = 3000   # batas aman panjang blok yang dibuang (char)


def buang_bagian_cara_pembayaran(teks: str) -> tuple:
    """
    Hapus blok DAFTAR dokumen syarat bayar: pasal 'cara pembayaran' di kontrak
    dan daftar lampiran 'terlampir kami sampaikan' di surat permohonan.
    Hanya dibuang bila benar berupa daftar (ada 'melampirkan'/'sebagai berikut'
    dekat heading) supaya kalimat biasa tak ikut terpotong. Pembatas halaman \f
    di dalam blok disisipkan kembali agar nomor halaman tidak geser.
    Return: (teks_bersih, teks_dibuang) — keduanya lowercase.
    """
    sisa    = []   # potongan teks yang dipertahankan
    dibuang = []   # potongan blok yang dibuang (untuk catatan info)
    pos = 0
    while True:
        m = _RE_AWAL_BAYAR.search(teks, pos)
        if not m:
            sisa.append(teks[pos:])
            break

        # konfirmasi: ini daftar dokumen, bukan sekadar menyebut "cara pembayaran"
        jendela = teks[m.end(): m.end() + 400]
        if not _RE_DAFTAR.search(jendela):
            sisa.append(teks[pos:m.end()])
            pos = m.end()
            continue

        # penanda akhir: (1) kalimat nidi — ikut dibuang sampai akhir baris;
        # (2) info transfer/rekening SETELAH heading; (3) cap _MAKS_BLOK.
        m_nidi = _RE_AKHIR_NIDI.search(teks, m.end())
        m_rek  = _RE_AKHIR_BAYAR.search(teks, m.end())
        if m_nidi and (m_nidi.end() - m.start()) <= _MAKS_BLOK:
            akhir = m_nidi.end()
        elif m_rek and (m_rek.start() - m.start()) <= _MAKS_BLOK:
            akhir = m_rek.start()
        else:
            # ponytail: fallback buta 3000 char — bisa ikut memotong awal
            # konten berikutnya bila daftar tanpa penanda akhir; upgrade =
            # penanda akhir eksplisit per jenis surat kalau kejadian nyata
            akhir = min(m.start() + _MAKS_BLOK, len(teks))

        blok = teks[m.start():akhir]
        sisa.append(teks[pos:m.start()])
        sisa.append("\f" * blok.count("\f"))   # jaga jumlah/nomor halaman
        dibuang.append(blok)
        pos = akhir

    return "".join(sisa), "\n".join(dibuang)


# ── Dokumen acuan (kontrak) — bukan deliverable ─────────────────────────────
# KHS (Kontrak Harga Satuan), Kontrak Rinci (KR) adalah dokumen ACUAN/kontrak,
# bukan deliverable syarat bayar. Body-nya menyebut hampir semua istilah syarat
# (lingkup kerja, spesifikasi) — di luar pasal "cara pembayaran" yang sudah
# dibuang — sehingga kalau ikut dicocokkan, syarat ke-detect LENGKAP padahal yang
# benar adalah file deliverable terpisah (false-positive). File acuan TIDAK
# di-OCR dan tidak dipakai untuk atribusi LENGKAP/ADA syarat lain; deteksinya
# CUKUP dari NAMA FILE saja.
_RE_ACUAN = re.compile(
    r"(?<![a-z0-9])khs(?![a-z0-9])|kontrak\s+harga\s+satuan"
)

# Subset acuan: file Kontrak Rinci (bukan KHS). Addendum SPK sering di-scan jadi
# satu bundel dengan KR, jadi KR tetap di-OCR penuh (untuk cek addendum) —
# hanya KHS yang OCR-nya dilewati.
# KR di nama deliverable hanya referensi; judul KR dan bundel addendum tetap acuan.
_RE_KR_SAJA = re.compile(
    r"kontrak[\s_-]*rinci|^\s*(?:\d+[.)]\s*(?:[a-z][.)]\s*)?)?kr(?![a-z0-9])"
    r"|(?<![a-z0-9])(?:add|addendum|adendum|amandemen)(?![a-z0-9])"
    r".*(?<![a-z0-9])kr(?![a-z0-9])"
)


def is_dokumen_acuan(nama_file: str) -> bool:
    return bool(_RE_ACUAN.search(nama_file.lower())) or is_kontrak_rinci(nama_file)


def is_kontrak_rinci(nama_file: str) -> bool:
    return bool(_RE_KR_SAJA.search(nama_file.lower()))


# ── Mutu teks embedded ──────────────────────────────────────────────────────
# Sebagian PDF (scan + layer teks bawaan) punya teks rusak: tiap glyph digandakan
# ("addendum" -> "aaddddeenndduumm") sehingga kata kunci tak pernah cocok dan
# file cuma ke-detect lewat nama. Rasio ini mendeteksinya supaya bisa di-OCR ulang.
# Teks normal ~0.02-0.04; teks dobel-garbled >0.3.

def _rasio_dobel(teks: str) -> float:
    a = [c for c in teks if c.isalpha()]
    if len(a) < 20:
        return 0.0
    pasang = sum(1 for i in range(0, len(a) - 1, 2) if a[i] == a[i + 1])
    return pasang / (len(a) // 2)


def bersihkan_noise_ocr(teks: str) -> str:
    """Remove only long OCR lines dominated by repeated short noise tokens."""
    if not teks:
        return teks

    if "\f" in teks:
        # per halaman, supaya pembatas \f tak ikut terbuang bersama baris noise
        return "\f".join(bersihkan_noise_ocr(h) for h in teks.split("\f"))

    bersih = []
    for baris in teks.splitlines(keepends=True):
        isi = baris.strip()
        if len(isi) < 80:
            bersih.append(baris)
            continue

        token = isi.split()
        alnum = [c.lower() for c in isi if c.isalnum()]
        if len(token) < 20 or len(alnum) < 60:
            bersih.append(baris)
            continue

        token_pendek = sum(len(t) <= 3 for t in token) / len(token)
        token_unik = len(set(token)) / len(token)

        # Noise OCR has many tiny tokens and a very small vocabulary.
        if len(token) >= 25 and token_pendek >= 0.9 and token_unik <= 0.55:
            continue
        bersih.append(baris)

    return "".join(bersih)


# ── Baca semua PDF ──────────────────────────────────────────────────────────

def _gabung_per_halaman(teks_digital: str, teks_ocr: str) -> str:
    """
    Gabung teks digital + OCR per halaman (pembatas \f) supaya tiap halaman
    fisik muncul sekali dan nomor halaman downstream = halaman fisik PDF.
    """
    dig = teks_digital.split("\f") if teks_digital else []
    ocr = teks_ocr.split("\f") if teks_ocr else []
    n = max(len(dig), len(ocr))
    dig += [""] * (n - len(dig))
    ocr += [""] * (n - len(ocr))
    return "\f".join(
        (d + "\n" + o) if (d.strip() and o.strip()) else (d or o)
        for d, o in zip(dig, ocr)
    )


def baca_semua_pdf(folder: str) -> dict:
    """
    Untuk setiap PDF ekstrak teks digital via pdfplumber.
    PDF tanpa teks digital (scan) di-OCR otomatis via Tesseract (sumber='ocr').
    Jika OCR tidak tersedia, ditandai sumber='scan' untuk verifikasi manual.
    Return dict: { nama_file: {teks, halaman, ukuran_kb, sumber} }
    """
    pdf_files = sorted([f for f in os.listdir(folder) if f.endswith(".pdf")])
    print(f"  Membaca {len(pdf_files)} file PDF:")
    if OCR_TERSEDIA:
        print(f"  OCR     : aktif (Tesseract {OCR_VERSI} '{OCR_LANG}', {OCR_DPI} dpi, "
              f"{OCR_WORKERS} core paralel, hasil di-cache | KHS dilewati, "
              f"KR di-OCR untuk cek addendum)\n")
    else:
        print(f"  {YELLOW}OCR     : NONAKTIF — PDF scan hanya dicek via nama file.{RESET}")
        print(f"  {YELLOW}          ({OCR_ERROR}){RESET}\n")

    hasil = {}
    for nama in pdf_files:
        path = os.path.join(folder, nama)
        halaman = 0
        teks_digital = ""
        hal_scan = 0          # halaman bergambar tapi teks minim (scan dalam bundel)
        hal_digital = []

        try:
            with pdfplumber.open(path) as pdf:
                halaman = len(pdf.pages)
                for pg in pdf.pages:
                    t = pg.extract_text() or ""
                    # halaman kosong tetap disimpan supaya indeks halaman
                    # sinkron dengan PDF fisik
                    hal_digital.append(t.lower() + "\n" if t.strip() else "")
                    if len(pg.images) >= 1 and len(t.strip()) < 60:
                        hal_scan += 1
        except Exception:
            pass
        teks_digital = "\f".join(hal_digital)

        # Mutu teks embedded buruk? -> kosong / huruf dobel-garbled / ada halaman
        # cuma gambar. Ketiganya bikin kata kunci gagal cocok meski isinya ada,
        # sehingga file cuma ke-detect lewat nama. OCR ulang untuk pulihkan isi.
        teks_kosong = not teks_digital.strip()
        mutu_buruk  = teks_kosong or _rasio_dobel(teks_digital) > 0.15 or hal_scan >= 1

        teks    = teks_digital
        sumber  = "digital"
        is_acuan = is_dokumen_acuan(nama)   # KHS dkk: acuan, bukan deliverable

        if not mutu_buruk:
            print(f"    [teks ] {nama} ({halaman} hal)")
        elif is_acuan and not (OCR_TERSEDIA and is_kontrak_rinci(nama)):
            # KHS (dokumen acuan): teksnya TIDAK dipakai untuk atribusi LENGKAP/ADA,
            # jadi OCR scan-nya yang berat = sia-sia. Lewati, pakai teks digital
            # seadanya (umumnya kontrak punya teks digital).
            # KR tidak dilewati: tetap di-OCR (jalur di bawah) untuk cek addendum
            # yang dibundel di dalamnya; atribusi syarat lain tetap dikecualikan.
            sumber = "digital" if teks_digital.strip() else "scan"
            print(f"    {CYAN}[acuan]{RESET} {nama} ({halaman} hal) — OCR dilewati (dokumen acuan)")
        elif OCR_TERSEDIA:
            tag = "ocr" if teks_kosong else "t+ocr"
            print(f"    [{tag:5}] {nama} ({halaman} hal) — OCR...", end="", flush=True)
            ocr_teks, dari_cache, error_ocr = "", False, None
            try:
                ocr_teks, dari_cache = ocr_pdf(path, folder, nama)
            except Exception as e:
                error_ocr = e
            if ocr_teks.strip():
                # gabung teks digital (bila ada) + hasil OCR -> recall maksimal
                # gabung per halaman -> nomor halaman = halaman fisik PDF
                teks   = (_gabung_per_halaman(teks_digital, ocr_teks)
                          if teks_digital.strip() else ocr_teks)
                sumber = "ocr" if teks_kosong else "digital+ocr"
                asal   = "cache" if dari_cache else f"{len(ocr_teks.split())} kata"
                print(f" {GREEN}OK{RESET} ({asal})")
            elif teks_kosong:
                sumber = "scan"
                pesan  = f"gagal: {error_ocr}" if error_ocr else "kosong"
                print(f" {YELLOW}{pesan} — verifikasi manual{RESET}")
            else:
                print(f" {YELLOW}OCR kosong — pakai teks digital{RESET}")
        elif teks_kosong:
            sumber = "scan"
            print(f"    [scan ] {nama} ({halaman} hal) — verifikasi manual")
        else:
            print(f"    [teks ] {nama} ({halaman} hal)")

        # Cache and retain raw OCR; only the matching copy gets noise cleanup.
        teks_cocok = bersihkan_noise_ocr(teks)
        # buang pasal "cara pembayaran" (daftar syarat) agar kontrak tak ke-detect
        teks_bersih, teks_abai = buang_bagian_cara_pembayaran(teks_cocok)

        hasil[nama] = {
            "teks": teks,                      # RAW text (before OCR cleanup/cache preservation)
            "teks_ocr_bersih": teks_cocok,     # cleaned OCR text for extraction
            "teks_bersih": teks_bersih,        # cleaned text (for syarat detection)
            "teks_abai": teks_abai,
            "halaman": halaman,
            "ukuran_kb": os.path.getsize(path) // 1024,
            "ada_teks": bool(teks_bersih.strip()),
            "sumber": sumber,
            "acuan": is_acuan,                 # KHS dkk: acuan, bukan deliverable
        }

    print()
    return hasil


# ── Deteksi syarat ──────────────────────────────────────────────────────────

PROXIMITY = 200                              # jarak maks antar kata kunci 1 pola (char)
_RE_TEMPLATE = re.compile(r"(format|contoh)\s+$")   # penanda template/contoh KOSONG
# ponytail: nomor berbentuk NNN.kode/...; tambah pola bila format vendor lain ditemukan.
_RE_NOMOR_DOKUMEN = re.compile(r"(?<!\d)\d{3,}\s*\.\s*[a-z]+\s*/")


def _pola_frase(frase: str) -> "re.Pattern":
    """
    Regex pencocok 'frase' dengan batas kata di sisi alfanumerik. Mencegah kata
    kunci pendek (mis. 'nidi', 'rlb', 'mom') ikut cocok di tengah token sampah
    OCR (mis. 'ponidiommimia'). Sisi non-alfanumerik (mis. '%') dibiarkan bebas.
    """
    kiri  = r"(?<![a-z0-9])" if frase[:1].isalnum()  else ""
    kanan = r"(?![a-z0-9])"  if frase[-1:].isalnum() else ""
    return re.compile(kiri + re.escape(frase) + kanan)


def _posisi_frase(teks: str, frase: str):
    """
    Posisi kemunculan pertama 'frase' sebagai isi nyata — BUKAN template
    'format ...'/'contoh ...' di kontrak — atau None. Pencocokan memakai
    batas kata supaya kata kunci pendek tak kena sampah OCR.
    """
    for m in _pola_frase(frase).finditer(teks):
        i = m.start()
        if not _RE_TEMPLATE.search(teks[max(0, i - 14):i]):
            return i
    return None


def _frase_muncul(teks: str, frase: str) -> bool:
    return _posisi_frase(teks, frase) is not None


def _posisi_pola(teks: str, pola: list):
    """
    Posisi match paling awal satu pola, atau None. Pola multi-kata: posisi =
    kemunculan kata pertama (non-template) yang seluruh kata lain hadir dalam
    jendela PROXIMITY di sekitarnya.
    """
    kws = [k.lower() for k in pola]
    if any(_posisi_frase(teks, k) is None for k in kws):
        return None
    if len(kws) == 1:
        return _posisi_frase(teks, kws[0])
    base = kws[0]
    for m in _pola_frase(base).finditer(teks):
        i = m.start()
        if _RE_TEMPLATE.search(teks[max(0, i - 14):i]):
            continue
        seg = teks[max(0, i - PROXIMITY): i + len(base) + PROXIMITY]
        if all(k in seg for k in kws[1:]):
            return i
    return None


def posisi_pola_teks(teks: str, pola_list: list) -> tuple:
    """Posisi match paling awal di antara semua pola: (posisi, label) / (None, None)."""
    # Mask nomor dengan panjang sama: posisi asli dan jendela proximity tidak bergeser.
    teks = _RE_NOMOR_DOKUMEN.sub(lambda m: " " * len(m.group()), teks)
    pos_terbaik, label_terbaik = None, None
    for pola in pola_list:
        p = _posisi_pola(teks, pola)
        if p is not None and (pos_terbaik is None or p < pos_terbaik):
            pos_terbaik = p
            label_terbaik = " + ".join(f'"{k}"' for k in pola)
    return pos_terbaik, label_terbaik


def cocok_pola_teks(teks: str, pola_list: list) -> tuple:
    p, label = posisi_pola_teks(teks, pola_list)
    return (p is not None), label


def cocok_nama_file(nama_file: str, nama_hint_list: list) -> tuple:
    nama_lower = nama_file.lower()
    for hint in nama_hint_list:
        if hint.lower() in nama_lower:
            return True, hint
    return False, None


def _pemenang_halaman(semua_pdf: dict, syarat_list: list) -> dict:
    """
    First-match-wins per halaman: tiap halaman diatribusikan HANYA ke syarat
    dengan match paling atas — daftar/penyebutan syarat lain di bawahnya tidak
    dihitung (mis. daftar lampiran surat permohonan yang lolos pemotongan).
    Return: { nama_file: { kode: (no_halaman_1based, label_kata_kunci) } };
    per syarat disimpan halaman kemenangan pertamanya.
    """
    menang = {}
    for nama_file, info in semua_pdf.items():
        if not info["ada_teks"]:
            continue
        per_file = {}
        for no, hal in enumerate(info["teks_bersih"].split("\f"), 1):
            kandidat = []   # (posisi, urutan_di_list, kode, label)
            for urut, item in enumerate(syarat_list):
                if info.get("acuan") and not item.get("izinkan_acuan", False):
                    continue
                p, label = posisi_pola_teks(hal, item["pola_teks"])
                if p is not None:
                    kandidat.append((p, urut, item["kode"], label))
            if not kandidat:
                continue
            kandidat.sort()   # posisi terkecil menang; seri -> urutan list
            _, _, kode, label = kandidat[0]
            per_file.setdefault(kode, (no, label))
        if per_file:
            menang[nama_file] = per_file
    return menang


def deteksi_syarat(semua_pdf: dict, syarat_list: list, menang: dict = None) -> dict:
    if menang is None:
        menang = _pemenang_halaman(semua_pdf, syarat_list)
    hasil = {}
    for item in syarat_list:
        kode          = item["kode"]
        pola_teks     = item["pola_teks"]
        nama_hint     = item["nama_hint"]
        izinkan_acuan = item.get("izinkan_acuan", False)

        cocok_teks = []
        cocok_nama = []

        for nama_file, info in semua_pdf.items():
            # Dokumen acuan (KHS/KR): ISI-nya bukan deliverable (sudah disaring
            # per-item di _pemenang_halaman), tapi NAMA file tetap dicek —
            # bundel "Kontrak Rinci, Adendum, Evaluasi, MoM.pdf" memang memuat
            # MoM/Evaluasi di dalamnya meski isinya tidak di-OCR.
            if kode in menang.get(nama_file, {}):
                no_hal, label = menang[nama_file][kode]
                cocok_teks.append((nama_file, label, info, no_hal))

            ok_nama, hint = cocok_nama_file(nama_file, nama_hint)
            # Syarat ber-flag nama_abaikan_acuan: nama file acuan tak dihitung
            # (mis. "ADD KHS" = addendum KHS, bukan addendum SPK).
            if ok_nama and info.get("acuan") and item.get("nama_abaikan_acuan"):
                ok_nama, hint = False, None
            # Syarat kontrak memakai classifier yang sama, bukan substring KR di deliverable.
            if izinkan_acuan:
                ok_nama = bool(info.get("acuan")) or is_dokumen_acuan(nama_file)
                hint = "dokumen acuan (KR/KHS)" if ok_nama else None
            if ok_nama and not any(n[0] == nama_file for n in cocok_teks):
                cocok_nama.append((nama_file, hint, info))

        # batasi atribusi: bila ada file yang namanya juga relevan, pakai itu saja
        # (mis. "nomor pokok wajib pajak" kebetulan ada di file lain — abaikan).
        if cocok_teks and nama_hint:
            relevan = [c for c in cocok_teks if cocok_nama_file(c[0], nama_hint)[0]]
            if relevan:
                cocok_teks = relevan

        file_names = [f[0] for f in cocok_teks]
        file_names += [f[0] for f in cocok_nama if f[0] not in file_names]
        if cocok_teks:
            info     = cocok_teks[0][2]
            sumber   = info["sumber"]
            label    = "LENGKAP (OCR)" if "ocr" in sumber else "LENGKAP"
            hasil[kode] = {
                "status": label,
                "file": ", ".join(file_names),
                "file_names": file_names,
                "detail": (
                    f"Kata kunci: {cocok_teks[0][1]} | "
                    f"sumber: {sumber} | "
                    f"hal {cocok_teks[0][3]}/{info['halaman']}, {info['ukuran_kb']} KB"
                ),
            }
        elif cocok_nama:
            info = cocok_nama[0][2]
            hasil[kode] = {
                "status": "ADA (nama file)",
                "file": ", ".join(file_names),
                "file_names": file_names,
                "detail": (
                    f"Nama mengandung '{cocok_nama[0][1]}' | "
                    f"sumber: {info['sumber']} | "
                    f"{info['halaman']} hal, {info['ukuran_kb']} KB"
                ),
            }
        else:
            # cek apakah syarat ini hanya disebut di pasal cara pembayaran ATAU
            # di body dokumen acuan (KHS) — keduanya bukan deliverable nyata.
            disebut_di = []
            if pola_teks:
                for nama_file, info in semua_pdf.items():
                    abai = info.get("teks_abai", "")
                    if abai and cocok_pola_teks(abai, pola_teks)[0]:
                        disebut_di.append(nama_file)
                    elif info.get("acuan") and cocok_pola_teks(info["teks_bersih"], pola_teks)[0]:
                        disebut_di.append(nama_file)

            if disebut_di:
                hasil[kode] = {
                    "status": "TIDAK ADA",
                    "file": "-",
                    "detail": (
                        "Tidak ada deliverable. Hanya tercantum di dokumen "
                        f"acuan / pasal cara pembayaran: {', '.join(disebut_di)}"
                    ),
                    "catatan_kontrak": True,
                }
            else:
                hasil[kode] = {
                    "status": "TIDAK ADA",
                    "file": "-",
                    "detail": "Tidak ada file yang cocok (isi maupun nama)",
                }

    return hasil


# ── Addendum di dalam bundel Kontrak Rinci ──────────────────────────────────
# Addendum SPK sering di-scan jadi satu file dengan KR (acuan), padahal isi
# acuan dikecualikan dari atribusi syarat. Cek khusus: halaman KR yang JUDUL-nya
# addendum (di awal halaman) DAN memuat nomor addendum "NNNN.Add" — kontrak asli
# tidak pernah memuat nomor addendum (terbit belakangan), jadi pasal boilerplate
# yang cuma menyebut kata "addendum" tidak ikut cocok.

_RE_JUDUL_ADDENDUM = re.compile(r"addendum|amandemen|adendum")
_RE_NOMOR_ADDENDUM = re.compile(r"\d{3,5}\s*\.\s*add(?![a-z])")


def deteksi_addendum_dalam_kr(semua_pdf: dict, hasil_diluar: dict) -> None:
    """Timpa hasil syarat (t) bila ada halaman addendum di dalam bundel KR."""
    if "LENGKAP" in hasil_diluar.get("t", {}).get("status", ""):
        return
    for nama, info in semua_pdf.items():
        if not info.get("acuan") or not is_kontrak_rinci(nama):
            continue
        for no, hal in enumerate(info.get("teks_ocr_bersih", "").split("\f"), 1):
            # ponytail: 300 char pertama = zona judul (kop surat + judul);
            # kalau kop panjang bikin luput, naikkan batasnya
            if (_RE_JUDUL_ADDENDUM.search(hal[:300])
                    and _RE_NOMOR_ADDENDUM.search(hal)):
                hasil_diluar["t"] = {
                    "status": ("LENGKAP (OCR)" if "ocr" in info["sumber"]
                               else "LENGKAP"),
                    "file": nama,
                    "file_names": [nama],
                    "detail": (
                        f"Addendum di dalam bundel KR | sumber: {info['sumber']} | "
                        f"hal {no}/{info['halaman']}, {info['ukuran_kb']} KB"
                    ),
                }
                return


# ── Konsistensi antar dokumen ───────────────────────────────────────────────

# ── Terbilang converter ──
_SATUAN = ["", "satu", "dua", "tiga", "empat", "lima", "enam",
           "tujuh", "delapan", "sembilan"]


def terbilang_dari_angka(n: int) -> str:
    """Convert integer rupiah to standard Indonesian number-words.
    Handles 0..millions. Does NOT append 'rupiah'.
    """
    if n == 0:
        return "nol"

    def _ratusan(angka: int) -> list:
        """Convert 0..999 to list of Indonesian words."""
        if angka == 0:
            return []
        kata = []
        ratus, sisa = divmod(angka, 100)
        if ratus:
            if ratus == 1:
                kata.append("seratus")
            else:
                kata.append(_SATUAN[ratus])
                kata.append("ratus")
        if sisa >= 11 and sisa <= 19:
            sisa2 = sisa - 10
            if sisa2 == 1:
                kata.append("sebelas")
            elif sisa2 == 0:
                kata.append("sepuluh")
            else:
                kata.append(_SATUAN[sisa2])
                kata.append("belas")
        else:
            puluh, sat = divmod(sisa, 10)
            if puluh:
                if puluh == 1:
                    kata.append("sepuluh")
                else:
                    kata.append(_SATUAN[puluh])
                    kata.append("puluh")
            if sat:
                kata.append(_SATUAN[sat])
        return kata

    if n < 0:
        return "minus " + terbilang_dari_angka(-n)

    # Group into millions, thousands, units
    miliar, sisa = divmod(n, 1_000_000_000)
    juta,  sisa = divmod(sisa, 1_000_000)
    ribu,  unit = divmod(sisa, 1_000)

    parts = []
    if miliar:
        parts.extend(_ratusan(miliar))
        parts.append("milyar")
    if juta:
        parts.extend(_ratusan(juta))
        parts.append("juta")
    if ribu:
        r = _ratusan(ribu)
        if ribu == 1:
            parts.append("seribu")
        else:
            parts.extend(r)
            parts.append("ribu")
    if unit:
        parts.extend(_ratusan(unit))

    return " ".join(parts)


# ── OCR halaman pertama (khusus KR) ──

def ocr_halaman_pertama(path: str, folder: str, nama: str) -> str:
    """
    OCR hanya halaman 1 PDF -> teks (lowercase).
    Hasil di-cache di .ocr_cache/<nama>.p1.txt.
    """
    cache_dir  = os.path.join(folder, OCR_CACHE_DIR)
    cache_file = os.path.join(cache_dir, nama + ".p1.txt")
    try:
        stat  = os.stat(path)
        stamp = (f"# stamp:{stat.st_size}:{int(stat.st_mtime)}:dpi{OCR_DPI}:max{OCR_MAX_PX}:"
                 f"{OCR_CONFIG}:{OCR_LANG}:v{OCR_VERSI}:p1")
    except Exception:
        return ""

    if os.path.isfile(cache_file):
        try:
            with open(cache_file, encoding="utf-8") as f:
                isi = f.read()
            if isi.startswith(stamp):
                return isi[len(stamp):].lstrip("\n")
        except Exception:
            pass

    if not OCR_TERSEDIA:
        return ""

    doc = fitz.open(path)
    try:
        img = _render_ocr(doc[0])  # halaman 1
    finally:
        doc.close()

    teks = pytesseract.image_to_string(img, lang=OCR_LANG, config=OCR_CONFIG).lower()

    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(stamp + "\n" + teks)
    except Exception:
        pass

    return teks


# ── Field extractors ──

import re as _re

# Fuzzy month matching — up to 1-char edit distance
_BULAN = [
    "januari", "februari", "maret", "april", "mei", "juni",
    "juli", "agustus", "september", "oktober", "november", "desember",
]


def _fuzzy_bulan(s: str):
    """Match month name fuzzily (≤1 char diff). Returns 1..12 or None."""
    s = s.strip().lower()
    # exact match dulu: "juli" berjarak 1 huruf dari "juni", tanpa ini
    # semua tanggal bulan juli terbaca sebagai juni
    if s in _BULAN:
        return _BULAN.index(s) + 1
    for i, bulan in enumerate(_BULAN, 1):
        if len(bulan) != len(s):
            continue
        diff = sum(1 for a, b in zip(bulan, s) if a != b)
        if diff <= 1:
            return i
    return None


_RE_TANGGAL_ANGKA = _re.compile(
    r"(\d{1,2})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{2,4})"
)

_RE_RUPIAH = _re.compile(
    r"(?:^|(?<=[:\s]))rp\.?\s*((?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:,\d{2})?)(?:\s|$|,|-)",
    _re.IGNORECASE
)

# Bare number pattern — only matches 3+ digit-groups separated by dots (e.g. 72.276.420)
# or a single number followed by ,- (like 72.276.420,-)
_RE_BARE_ANGKA = _re.compile(
    r"(?:^|(?<=[:\s]))(\d{1,3}\.\d{3}(?:\.\d{3})*)(?:,-)?(?:,\d{2})?",
    _re.IGNORECASE
)


# Nomor rekening bank (mis. "no. rekening : 146.000.3793796") berformat mirip
# nilai rupiah, jadi ikut tertangkap _RE_BARE_ANGKA dan bisa lebih besar dari
# nilai tagihan. Angka yang didahului label rekening/a.n. bank harus diabaikan.
_RE_LABEL_REKENING = _re.compile(
    r"(?<![a-z])(?:rek(?:ening)?|a/c)\s*\.?\s*:?\s*$"
)


def _bukan_rekening(teks: str, pos: int) -> bool:
    """True bila angka pada posisi pos BUKAN nomor rekening bank."""
    return not _RE_LABEL_REKENING.search(teks[max(0, pos - 30):pos])


def _uang_raw_ke_int(raw: str) -> int:
    if "," in raw and "." not in raw:
        parts = raw.split(",")
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
            raw = "".join(parts)
        else:
            raw = raw.replace(",", ".")
    else:
        raw = raw.replace(".", "").replace(",", ".")
    return int(float(raw))

_RE_KOTA_TANGGAL = _re.compile(
    r"([a-z]+)\s*,\s*(\d{1,2})\s+(januari|februari|maret|april|mei|juni|"
    r"juli|agustus|september|oktober|november|desember)\s+(\d{4})",
    _re.IGNORECASE
)


def _normalize_date(day: int, month: int, year: int):
    """Return YYYY-MM-DD string or None if invalid."""
    if year < 100:
        year += 2000
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


# "<no kontrak>/2026 tanggal 8 mei 2026" — tanggal referensi dokumen lain
# (mis. tanggal KR di deskripsi barang Faktur), bukan tanggal dokumen ini
_RE_TANGGAL_REFERENSI = _re.compile(r"(?:19|20)\d{2}\s+tanggal\s*:?\s*$")


def _tanggal_referensi(teks_lower: str, pos: int) -> bool:
    return bool(_RE_TANGGAL_REFERENSI.search(teks_lower[max(0, pos - 30):pos]))


def ekstrak_tanggal(teks: str) -> list:
    """
    Extract all distinct dates from text. Prefers dates after city-name anchor.
    Returns sorted list of YYYY-MM-DD strings, or empty list.
    """
    found = set()
    teks_lower = teks.lower()
    # anchor: kota, tanggal bulan tahun
    for m in _RE_KOTA_TANGGAL.finditer(teks_lower):
        _, d, bulan_str, y = m.groups()
        bulan = _fuzzy_bulan(bulan_str)
        if bulan is None:
            continue
        d = int(d)
        y = int(y)
        nd = _normalize_date(d, bulan, y)
        if nd:
            found.add(nd)
    # angka: dd[-/]mm[-/]yy[yy]
    for m in _RE_TANGGAL_ANGKA.finditer(teks_lower):
        if _tanggal_referensi(teks_lower, m.start()):
            continue
        d, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        nd = _normalize_date(d, b, y)
        if nd:
            found.add(nd)
    # text: d bulan yyyy
    for m in _re.finditer(r"(\d{1,2})\s+(januari|februari|maret|april|mei|juni|"
                          r"juli|agustus|september|oktober|november|desember)\s+(\d{4})",
                          teks_lower):
        if _tanggal_referensi(teks_lower, m.start()):
            continue
        d, bulan_str, y = int(m.group(1)), m.group(2), int(m.group(3))
        bulan = _fuzzy_bulan(bulan_str)
        if bulan is None:
            continue
        nd = _normalize_date(d, bulan, y)
        if nd:
            found.add(nd)
    return sorted(found)


def ekstrak_nilai_rp(teks: str, label_anchor: str = None):
    """
    Extract largest Rp value near anchor label, or largest overall.
    For e-Faktur labels ('harga jual', 'jumlah ppn'), searches a broader
    window (1800 chars) since table columns are spaced far apart.
    Returns integer rupiah (without decimals) or None.
    """
    # Try Rp-prefixed matches first, fallback to bare formatted numbers (e.g. 72.276.420)
    teks_lower = teks.lower()
    matches = list(_RE_RUPIAH.finditer(teks_lower))
    if not matches:
        # bare number bisa berupa nomor rekening — buang yang berlabel rekening
        matches = [m for m in _RE_BARE_ANGKA.finditer(teks_lower)
                   if _bukan_rekening(teks_lower, m.start(1))]
    if not matches:
        return None

    def _to_int(m):
        try:
            return _uang_raw_ke_int(m.group(1))
        except ValueError:
            return 0

    if label_anchor:
        # Prefer matches near the anchor
        anchor_pos = teks.lower().find(label_anchor.lower())
        if anchor_pos != -1:
            # Use wider window for faktur (1800) than for surat (300)
            window = 1800 if label_anchor in ("harga jual", "jumlah ppn") else 300
            nearby = [m for m in matches
                      if abs(m.start() - anchor_pos) < window]
            if nearby:
                return max(_to_int(m) for m in nearby)

    return max(_to_int(m) for m in matches)


def ekstrak_terbilang(teks: str):
    """Extract terbilang text between 'terbilang : #' and 'rupiah #'."""
    m = _re.search(
        r"terbilang\s*[:#]?\s*#\s*(.*?)\s*#\s*rupiah",
        teks.lower(), _re.DOTALL
    )
    if m:
        return m.group(1).strip()
    # Fallback: terbilang: ... rupiah (no #)
    m = _re.search(
        r"terbilang\s*[:#]?\s*(.*?)\s*rupiah",
        teks.lower(), _re.DOTALL
    )
    if m:
        return m.group(1).strip()
    return None


_RE_KONTRAK = _re.compile(
    r"(\d{3,5}\.(?:spk|pj)[a-z0-9./ -]+\d{4})",
    _re.IGNORECASE
)

_RE_KONTRAK_LABEL = _re.compile(
    r"nomor\s+.+?:?\s*(\d{3,5}\.(?:spk|pj)[a-z0-9./ -]+\d{4})",
    _re.IGNORECASE
)

_OCR_CHAR_MAP = str.maketrans({
    "o": "0", "O": "0", "l": "1", "i": "1", "I": "1",
})


def _normalize_no_kontrak(s: str) -> str:
    """Normalize whitespace and OCR confusables for comparison."""
    s = s.strip().lower()
    s = _re.sub(r"\s+", "", s)
    return s.translate(_OCR_CHAR_MAP)


def ekstrak_no_kontrak(teks: str):
    """Extract SPK/PJ contract number. Returns raw string or None.
    Prefers label-anchored match (near Nomor label), then SPK over PJ.
    """
    def _bersihkan_suffix(raw: str) -> str:
        # OCR surat sering menempelkan tanggal setelah nomor kontrak, kadang
        # tanpa kata "tanggal" (mis. `.../2026 08 mei 2026`).
        raw = re.split(r"\s+(?:tanggal|tgl)\b", raw.strip(), maxsplit=1,
                       flags=re.IGNORECASE)[0].strip()
        return re.sub(
            r"\s+\d{1,2}\s+(?:januari|februari|maret|april|mei|juni|"
            r"juli|agustus|september|oktober|november|desember)\s+\d{4}.*$",
            "", raw, flags=re.IGNORECASE,
        ).strip()

    # Strategy 1: number after label containing "Nomor"
    for m in _RE_KONTRAK_LABEL.finditer(teks.lower()):
        return _bersihkan_suffix(m.group(1))

    # Strategy 2: find all SPK/PJ patterns, prefer SPK over PJ
    all_matches = list(_RE_KONTRAK.finditer(teks.lower()))
    if not all_matches:
        return None

    # Prefer SPK over PJ, prefer longer (more complete)
    prefer = []
    for m in all_matches:
        raw = _bersihkan_suffix(m.group(1))
        if 'spk' in raw.lower():
            prefer.append(('spk', raw))
    if not prefer:
        for m in all_matches:
            raw = _bersihkan_suffix(m.group(1))
            if 'pj' in raw.lower():
                prefer.append(('pj', raw))
    if not prefer:
        prefer = [('', _bersihkan_suffix(m.group(1))) for m in all_matches]

    prefer.sort(key=lambda x: len(x[1]), reverse=True)
    return prefer[0][1][:60]


def ekstrak_ref_tanggal_kontrak(teks: str):
    """Tanggal yang menempel pada nomor kontrak, mis. BASTB
    'Kontrak Rinci No. X, Tanggal 8 Mei 2026' atau KR
    'nomor ... X <newline> tanggal 08 mei 2026'.
    Return 'YYYY-MM-DD' atau None.
    """
    t = teks.lower()
    m = _RE_KONTRAK.search(t)
    if not m:
        return None
    # _RE_KONTRAK rakus dan bisa ikut menelan tanggalnya, jadi cari tanggal
    # di dalam span match + 60 karakter setelahnya
    ctx = t[m.start():m.end() + 60]
    md = _re.search(r"(\d{1,2})\s+(januari|februari|maret|april|mei|juni|"
                    r"juli|agustus|september|oktober|november|desember)\s+(\d{4})",
                    ctx)
    if md:
        bulan = _fuzzy_bulan(md.group(2))
        if bulan:
            return _normalize_date(int(md.group(1)), bulan, int(md.group(3)))
    md = _RE_TANGGAL_ANGKA.search(ctx[len(m.group(1)):])
    if md:
        return _normalize_date(int(md.group(1)), int(md.group(2)),
                               int(md.group(3)))
    return None


def ekstrak_kode_barang(teks: str):
    """Extract the actual six-digit goods/service code from e-Faktur text."""
    teks = teks.lower()
    label = r"kode\s*(?:[/\-]\s*)?(?:barang\s*[/\-]?\s*jasa|barang|jasa)"

    # Standard layout: the code follows the label directly.
    m = _re.search(label + r"\s*[:#]?\s*(\d{6})\b", teks)
    if m:
        return m.group(1)

    # Some PDF text layers put the value on a later table line.
    for m_label in _re.finditer(label, teks):
        ctx = teks[m_label.end():m_label.end() + 1000]
        m_code = _re.search(r"(?<!\d)(\d{6})(?!\d)", ctx)
        if m_code:
            return m_code.group(1)

    # OCR/PDF tables can separate the header from its row by many characters.
    for m_kode in _re.finditer(r"\bkode\b", teks):
        ctx = teks[m_kode.end():m_kode.end() + 1000]
        m_code = _re.search(r"(?<!\d)(\d{6})(?!\d)", ctx)
        if m_code:
            return m_code.group(1)
    return None


# ── Aturan konsistensi antar dokumen ──
ATURAN_KONSISTENSI = [
    {
        "kode": "K1",
        "nama": "Tanggal Permohonan = Faktur Pajak = Kuitansi",
        "fungsi": "cek_tanggal_sama",
    },
    {
        "kode": "K2",
        "nama": "Nilai tagihan Permohonan = Kuitansi",
        "fungsi": "cek_nilai_sama",
    },
    {
        "kode": "K3",
        "nama": "Terbilang Permohonan = Kuitansi",
        "fungsi": "cek_terbilang_sama",
    },
    {
        "kode": "K4",
        "nama": "Faktur Pajak (HJP + PPN) = Nilai tagihan",
        "fungsi": "cek_faktur_sama_tagihan",
    },
    {
        "kode": "K5",
        "nama": "Kode barang/jasa = 010000",
        "fungsi": "cek_kode_barang",
    },
    {
        "kode": "K6",
        "nama": "Terbilang sesuai nilai numerik",
        "fungsi": "cek_terbilang",
    },
    {
        "kode": "K7",
        "nama": "No. kontrak Permohonan = KR halaman 1",
        "fungsi": "cek_no_kontrak",
    },
    {
        "kode": "K8",
        "nama": "No. & tanggal kontrak BASTB = KR",
        "fungsi": "cek_bastb_kontrak",
    },
]


def _cari_file(hasil: dict, kode_syarat: str):
    """Return filename attributed to a given syarat code, or None."""
    r = hasil.get(kode_syarat)
    if not r:
        return None
    f = r.get("file", "").strip()
    if not f or f == "-":
        return None
    return f


def _cari_file_names(hasil: dict, kode_syarat: str) -> list:
    """Return exact attributed filenames, preserving commas in names."""
    r = hasil.get(kode_syarat)
    if not r:
        return []
    names = r.get("file_names")
    if names:
        return list(names)
    f = r.get("file", "").strip()
    return [f] if f and f != "-" else []


def _cari_info(semua_pdf: dict, hasil: dict, kode_syarat: str):
    """Return the PDF info dict for the file attributed to a syarat code."""
    names = _cari_file_names(hasil, kode_syarat)
    if names:
        return semua_pdf.get(names[0])
    fn = _cari_file(hasil, kode_syarat)
    if not fn:
        return None
    # Backward compatibility for older result dictionaries.
    fn = fn.split(",")[0].strip()
    return semua_pdf.get(fn)


def _teks_untuk_ekstraksi(info: dict) -> str:
    """Return cleaned OCR text while supporting older result dictionaries."""
    return info.get("teks_ocr_bersih", info["teks"])


def cek_tanggal_sama(semua_pdf: dict, hasil: dict) -> dict:
    """K1."""
    files = {
        "permohonan": ("a", "Permohonan Bayar"),
        "faktur": ("c", "Faktur Pajak"),
        "kuitansi": ("b", "Kuitansi"),
    }
    tanggal = {}
    for key, (skode, slabel) in files.items():
        info = _cari_info(semua_pdf, hasil, skode)
        if info is None:
            tanggal[key] = ("DILEWATI", f"{slabel} tidak ditemukan")
        else:
            teks = _teks_untuk_ekstraksi(info)
            tgl = ekstrak_tanggal(teks)
            if not tgl:
                tanggal[key] = ("PERLU CEK MANUAL",
                                f"tanggal tidak terbaca di {info.get('nama', slabel)}")
            else:
                tanggal[key] = ("OK", tgl[-1])  # newest date

    # Compare — dokumen yang tanggalnya tak terbaca memaksa cek manual,
    # jangan lapor SESUAI hanya dari dokumen yang kebetulan terbaca
    vals = [v for k, v in tanggal.items() if v[0] == "OK"]
    if len(vals) < 2 or any(v[0] == "PERLU CEK MANUAL" for v in tanggal.values()):
        detail = " | ".join(
            f"{k}: {v[1]}" for k, v in tanggal.items()
        )
        return {"status": "PERLU CEK MANUAL", "detail": detail}

    dates = [v[1] for v in vals]
    if len(set(dates)) == 1:
        detail = " | ".join(
            f"{k}: {v[1]}" for k, v in tanggal.items()
        )
        return {"status": "SESUAI", "detail": detail}
    else:
        detail = " | ".join(
            f"{k}: {v[1]}" for k, v in tanggal.items()
        )
        return {"status": "TIDAK SESUAI", "detail": detail}


def cek_nilai_sama(semua_pdf: dict, hasil: dict) -> dict:
    """K2: nilai Permohonan == Kuitansi."""
    nilai = {}
    for key, skode, label, anchor in [
        ("permohonan", "a", "Permohonan Bayar", "sebesar"),
        ("kuitansi", "b", "Kuitansi", "uang sejumlah"),
    ]:
        info = _cari_info(semua_pdf, hasil, skode)
        if info is None:
            nilai[key] = None
        else:
            v = ekstrak_nilai_rp(_teks_untuk_ekstraksi(info), anchor)
            if v is None:
                v = ekstrak_nilai_rp(_teks_untuk_ekstraksi(info))
            nilai[key] = v

    if nilai["permohonan"] is None or nilai["kuitansi"] is None:
        missing = [k for k, v in nilai.items() if v is None]
        return {"status": "PERLU CEK MANUAL",
                "detail": f"Nilai tidak terbaca: {', '.join(missing)}"}

    if nilai["permohonan"] == nilai["kuitansi"]:
        return {"status": "SESUAI",
                "detail": f"Permohonan: Rp {nilai['permohonan']:,} | "
                          f"Kuitansi: Rp {nilai['kuitansi']:,}"}
    else:
        return {"status": "TIDAK SESUAI",
                "detail": f"Permohonan: Rp {nilai['permohonan']:,} ≠ "
                          f"Kuitansi: Rp {nilai['kuitansi']:,}"}


# Angka berformat uang saja (ribuan bertitik/koma, mis. 7.590.522 / 7.590.522.00).
# Desimal 2 digit di luar capture; boundary menolak potongan angka panjang
# seperti nomor referensi "6815.0098...".
_RE_UANG_GROUPED = _re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+)(?:[.,]\d{2})?(?!\d)"
)


def _ekstrak_angka_setelah_label(teks: str, label: str, jarak_max: int = 300):
    """Find a money value near label: first match after it, else nearest before.

    OCR tabel e-Faktur kadang mengeluarkan kolom nilai SEBELUM baris labelnya
    (kasus 0098.SPK), jadi bila tidak ada angka uang setelah label, cari angka
    uang terdekat sebelum label.
    """
    pos = teks.lower().find(label.lower())
    if pos == -1:
        return None
    potong = teks[pos:pos + jarak_max]
    m = _RE_RUPIAH.search(potong) or _RE_UANG_GROUPED.search(potong)
    if m:
        try:
            return _uang_raw_ke_int(m.group(1))
        except ValueError:
            pass
    sebelum = teks[max(0, pos - 200):pos]
    matches = (list(_RE_RUPIAH.finditer(sebelum))
               + list(_RE_UANG_GROUPED.finditer(sebelum)))
    if matches:
        m = max(matches, key=lambda x: x.start())
        try:
            return _uang_raw_ke_int(m.group(1))
        except ValueError:
            pass
    return None


def cek_faktur_sama_tagihan(semua_pdf: dict, hasil: dict) -> dict:
    """K4: Harga Jual/Penggantian + Jumlah PPN (Faktur) == nilai tagihan."""
    info_faktur = _cari_info(semua_pdf, hasil, "c")
    if info_faktur is None:
        return {"status": "DILEWATI", "detail": "Faktur Pajak tidak ditemukan"}
    teks = _teks_untuk_ekstraksi(info_faktur)
    # Use anchored extraction: look for numbers after the label (supports both Rp-prefixed and bare numbers)
    hjp = _ekstrak_angka_setelah_label(teks, "harga jual", 1800)
    ppn = _ekstrak_angka_setelah_label(teks, "jumlah ppn", 600)
    if hjp is None or ppn is None:
        return {"status": "PERLU CEK MANUAL",
                "detail": "Harga Jual/Penggantian atau Jumlah PPN tidak terbaca di Faktur"}
    total_faktur = hjp + ppn

    # Nilai tagihan from Permohonan or Kuitansi (prefer Kuitansi)
    info_kuitansi = _cari_info(semua_pdf, hasil, "b")
    nilai_tagihan = None
    if info_kuitansi:
        nilai_tagihan = ekstrak_nilai_rp(info_kuitansi["teks"])
    if nilai_tagihan is None:
        info_perm = _cari_info(semua_pdf, hasil, "a")
        if info_perm:
            nilai_tagihan = ekstrak_nilai_rp(info_perm["teks"])
    if nilai_tagihan is None:
        return {"status": "PERLU CEK MANUAL",
                "detail": "Nilai tagihan (Permohonan/Kuitansi) tidak terbaca"}

    if total_faktur == nilai_tagihan:
        return {"status": "SESUAI",
                "detail": f"HJP + PPN = Rp {total_faktur:,} = tagihan Rp {nilai_tagihan:,}"}
    else:
        return {"status": "TIDAK SESUAI",
                "detail": f"HJP + PPN = Rp {total_faktur:,} ≠ tagihan Rp {nilai_tagihan:,}"}


def cek_tidak_ada_dpp(semua_pdf: dict, hasil: dict) -> dict:
    """Legacy DPP/PPN check; no longer shown in the consistency report."""
    dubious = []
    for skode, slabel in [("a", "Permohonan Bayar"), ("b", "Kuitansi")]:
        info = _cari_info(semua_pdf, hasil, skode)
        if info is not None:
            teks = _teks_untuk_ekstraksi(info)
            for pat in ["dpp", "dasar pengenaan pajak", "ppn"]:
                if pat in teks:
                    # Heuristic: if the word appears near a number, it's a
                    # price breakdown, not just a passing mention.
                    for m in _re.finditer(pat, teks):
                        ctx = teks[max(0, m.start()-5):m.end()+80]
                        if _re.search(r"\d[\d.,]*\d", ctx):
                            dubious.append(f"{slabel}: '...{ctx.strip()[:100]}...'")
                            break
    if dubious:
        return {"status": "PERLU CEK MANUAL",
                "detail": "Rincian pajak ditemukan di: " + " | ".join(dubious)}
    return {"status": "SESUAI", "detail": "Tidak ada rincian DPP/PPN di surat tagihan"}


def cek_kode_barang(semua_pdf: dict, hasil: dict) -> dict:
    """K5: kode barang/jasa == 010000."""
    info = _cari_info(semua_pdf, hasil, "c")
    if info is None:
        return {"status": "DILEWATI", "detail": "Faktur Pajak tidak ditemukan"}
    kode = ekstrak_kode_barang(_teks_untuk_ekstraksi(info))
    if kode is None:
        return {"status": "PERLU CEK MANUAL",
                "detail": "Kode barang/jasa tidak terbaca di Faktur Pajak"}
    if kode == "010000":
        return {"status": "SESUAI", "detail": f"Kode barang/jasa = {kode}"}
    else:
        return {"status": "TIDAK SESUAI",
                "detail": f"Kode barang/jasa = {kode} (harus 010000)"}


def _terbilang_match(terbilang_teks: str, nilai: int) -> bool:
    """Check that terbilang words match expected words for nilai."""
    expected = terbilang_dari_angka(nilai)
    # Tokenize both. "miliar" (ejaan KBBI) = "milyar" (ejaan lama) — dokumen
    # boleh pakai keduanya, generator kita memakai "milyar".
    exp_tokens = expected.split()
    # Token huruf saja: kutip OCR (“empat), koma, dsb. tak boleh merusak match.
    got_tokens = _re.findall(r"[a-z]+", terbilang_teks.replace("miliar", "milyar"))

    # OCR-tolerant: each expected word must appear in order in got_tokens
    it = iter(got_tokens)
    for ew in exp_tokens:
        found = False
        for gw in it:
            if gw == ew:
                found = True
                break
        if not found:
            return False
    return True


_KATA_BILANGAN = frozenset(
    "nol satu dua tiga empat lima enam tujuh delapan sembilan sepuluh "
    "sebelas belas puluh ratus seratus ribu seribu juta milyar triliun".split()
)


def _token_bilangan(teks: str) -> list:
    """Token kata-bilangan saja — noise OCR ('amount in words', tanda baca,
    kutip) di sekitar terbilang tidak boleh merusak perbandingan."""
    kata = _re.findall(r"[a-z]+", teks.lower().replace("miliar", "milyar"))
    return [k for k in kata if k in _KATA_BILANGAN]


def cek_terbilang_sama(semua_pdf: dict, hasil: dict) -> dict:
    """K3: terbilang Permohonan == terbilang Kuitansi."""
    tb = {}
    for key, skode, slabel in [("permohonan", "a", "Permohonan Bayar"),
                               ("kuitansi", "b", "Kuitansi")]:
        info = _cari_info(semua_pdf, hasil, skode)
        if info is None:
            return {"status": "DILEWATI", "detail": f"{slabel} tidak ditemukan"}
        t = ekstrak_terbilang(_teks_untuk_ekstraksi(info))
        if t is None:
            return {"status": "PERLU CEK MANUAL",
                    "detail": f"Terbilang tidak terbaca di {slabel}"}
        tb[key] = t

    if _token_bilangan(tb["permohonan"]) == _token_bilangan(tb["kuitansi"]):
        return {"status": "SESUAI",
                "detail": "Terbilang Permohonan = Kuitansi"}
    return {"status": "TIDAK SESUAI",
            "detail": f"Permohonan: '{tb['permohonan'][:60]}' ≠ "
                      f"Kuitansi: '{tb['kuitansi'][:60]}'"}


def cek_terbilang(semua_pdf: dict, hasil: dict) -> dict:
    """K6: terbilang matches numeric value."""
    issues = []
    for skode, slabel, anchor in [
        ("a", "Permohonan Bayar", "sebesar"),
        ("b", "Kuitansi", "uang sejumlah"),
    ]:
        info = _cari_info(semua_pdf, hasil, skode)
        if info is None:
            continue
        teks = _teks_untuk_ekstraksi(info)
        terbilang = ekstrak_terbilang(teks)
        if terbilang is None:
            continue  # not all docs have terbilang
        nilai = ekstrak_nilai_rp(teks, anchor)
        if nilai is None:
            continue
        if not _terbilang_match(terbilang, nilai):
            issues.append(f"{slabel}: terbilang '{terbilang[:60]}...' ≠ Rp {nilai:,}")

    if not issues:
        return {"status": "SESUAI", "detail": "Terbilang sesuai dengan nilai numerik"}
    else:
        return {"status": "TIDAK SESUAI",
                "detail": " | ".join(issues)}


def _teks_kr_halaman1(semua_pdf: dict, hasil: dict, folder: str):
    """Teks halaman 1 KR, atau None bila KR tidak ditemukan.

    Always use OCR page 1 for clean extraction, because KR digital text
    layers often contain junk/noise from scanning. Prefer Kontrak Rinci/KR
    files over KHS (KHS page 1 often lacks the contract number).
    """
    info_kr = _cari_info(semua_pdf, hasil, "z")
    if info_kr is None:
        return None
    kr_file_names = _cari_file_names(hasil, "z")
    teks_kr = ""
    if kr_file_names:
        # Prefer "Kontrak Rinci" or "KR" over "KHS"
        candidates = kr_file_names
        kr_file_first = None
        for c in candidates:
            name_lower = c.lower()
            if "kontrak rinci" in name_lower or name_lower.startswith("kr.") or name_lower == "kr.pdf":
                kr_file_first = c
                break
        if kr_file_first is None and candidates:
            kr_file_first = candidates[0]
        path = os.path.join(folder, kr_file_first)
        if os.path.isfile(path):
            teks_kr = bersihkan_noise_ocr(
                ocr_halaman_pertama(path, folder, kr_file_first)
            )
    if not teks_kr.strip():
        # Fallback: use whatever digital text exists from the first KR file
        teks_kr = _teks_untuk_ekstraksi(info_kr)
    return teks_kr


def cek_no_kontrak(semua_pdf: dict, hasil: dict, folder: str) -> dict:
    """K7: no kontrak Permohonan == KR page 1."""
    info_perm = _cari_info(semua_pdf, hasil, "a")
    if info_perm is None:
        return {"status": "DILEWATI", "detail": "Permohonan Bayar tidak ditemukan"}
    no_perm = ekstrak_no_kontrak(_teks_untuk_ekstraksi(info_perm))
    if no_perm is None:
        return {"status": "PERLU CEK MANUAL",
                "detail": "No. kontrak tidak terbaca di Permohonan Bayar"}

    teks_kr = _teks_kr_halaman1(semua_pdf, hasil, folder)
    if teks_kr is None:
        return {"status": "PERLU CEK MANUAL",
                "detail": "Kontrak Rinci (KR) tidak ditemukan"}

    no_kr = ekstrak_no_kontrak(teks_kr)
    if no_kr is None:
        return {"status": "PERLU CEK MANUAL",
                "detail": "No. kontrak tidak terbaca di KR halaman 1"}

    norm_perm = _normalize_no_kontrak(no_perm)
    norm_kr = _normalize_no_kontrak(no_kr)
    if norm_perm == norm_kr:
        return {"status": "SESUAI",
                "detail": f"Permohonan: {no_perm} = KR: {no_kr}"}
    else:
        return {"status": "TIDAK SESUAI",
                "detail": f"Permohonan: {no_perm} ≠ KR: {no_kr}"}


def cek_bastb_kontrak(semua_pdf: dict, hasil: dict, folder: str) -> dict:
    """K8: no & tanggal kontrak yang disebut BASTB == KR page 1."""
    info = _cari_info(semua_pdf, hasil, "g")
    if info is None:
        return {"status": "DILEWATI", "detail": "BASTB tidak ditemukan"}
    teks_bastb = _teks_untuk_ekstraksi(info)
    no_bastb = ekstrak_no_kontrak(teks_bastb)
    if no_bastb is None:
        return {"status": "PERLU CEK MANUAL",
                "detail": "No. kontrak tidak terbaca di BASTB"}
    tgl_bastb = ekstrak_ref_tanggal_kontrak(teks_bastb)

    teks_kr = _teks_kr_halaman1(semua_pdf, hasil, folder)
    if teks_kr is None:
        return {"status": "PERLU CEK MANUAL",
                "detail": "Kontrak Rinci (KR) tidak ditemukan"}
    no_kr = ekstrak_no_kontrak(teks_kr)
    if no_kr is None:
        return {"status": "PERLU CEK MANUAL",
                "detail": "No. kontrak tidak terbaca di KR halaman 1"}
    tgl_kr = ekstrak_ref_tanggal_kontrak(teks_kr)

    if _normalize_no_kontrak(no_bastb) != _normalize_no_kontrak(no_kr):
        return {"status": "TIDAK SESUAI",
                "detail": f"No. kontrak BASTB: {no_bastb} ≠ KR: {no_kr}"}

    if tgl_bastb is None or tgl_kr is None:
        mana = "BASTB" if tgl_bastb is None else "KR"
        return {"status": "PERLU CEK MANUAL",
                "detail": f"No. kontrak sama ({no_bastb}), "
                          f"tapi tanggal kontrak tidak terbaca di {mana}"}
    if tgl_bastb == tgl_kr:
        return {"status": "SESUAI",
                "detail": f"No. kontrak {no_bastb}, tanggal {tgl_bastb} "
                          f"sama di BASTB dan KR"}
    if tgl_bastb[4:] == tgl_kr[4:]:
        # Hari+bulan sama, hanya tahun beda — digit tahun di scan KR sering
        # salah baca OCR (kasus 0098.SPK: 2026 terbaca 2025), jangan vonis
        # TIDAK SESUAI dari OCR yang meragukan
        return {"status": "PERLU CEK MANUAL",
                "detail": f"No. kontrak sama, tanggal BASTB {tgl_bastb} vs "
                          f"KR {tgl_kr} — beda tahun saja, kemungkinan salah "
                          f"baca OCR, cek manual"}
    return {"status": "TIDAK SESUAI",
            "detail": f"No. kontrak sama, tapi tanggal BASTB {tgl_bastb} "
                      f"≠ KR {tgl_kr}"}


def cek_konsistensi(semua_pdf: dict, hasil: dict, folder: str) -> dict:
    """Run all consistency rules. Returns dict {kode: {status, detail}}."""
    hasil_at = {}
    for at in ATURAN_KONSISTENSI:
        nama = at["fungsi"]
        if nama in ("cek_no_kontrak", "cek_bastb_kontrak"):
            # K7/K8 perlu folder untuk OCR ulang halaman 1 KR
            hasil_at[at["kode"]] = globals()[nama](semua_pdf, hasil, folder)
        else:
            hasil_at[at["kode"]] = globals()[nama](semua_pdf, hasil)
    return hasil_at


# ── Tampilkan hasil konsistensi ──

def _cetak_item_konsistensi(item: dict, hasil_at: dict) -> None:
    kode   = item["kode"]
    nama   = item["nama"]
    r      = hasil_at[kode]
    status = r["status"]

    if status == "SESUAI":
        warna = GREEN
        ikon  = "[SESUAI]"
    elif status == "TIDAK SESUAI":
        warna = RED
        ikon  = "[TIDAK SESUAI]"
    elif status == "DILEWATI":
        warna = YELLOW
        ikon  = "[DILEWATI]"
    else:
        warna = YELLOW
        ikon  = "[PERLU CEK MANUAL]"

    print(f"{warna}{BOLD}  ({kode}) {nama}{RESET}")
    print(f"       Status : {warna}{ikon}{RESET}")
    print(f"       {r['detail']}")
    print()


def tampilkan_konsistensi(folder: str, aturan_list: list,
                          hasil_at: dict) -> None:
    print(f"{'─' * 70}")
    print(f"{BOLD}{CYAN}  [C] KONSISTENSI ANTAR DOKUMEN{RESET}")
    print(f"{'─' * 70}\n")
    for item in aturan_list:
        _cetak_item_konsistensi(item, hasil_at)

    sesuai   = sum(1 for r in hasil_at.values() if r["status"] == "SESUAI")
    tidak    = sum(1 for r in hasil_at.values() if r["status"] == "TIDAK SESUAI")
    manual   = sum(1 for r in hasil_at.values()
                   if r["status"] == "PERLU CEK MANUAL")
    dilewati = sum(1 for r in hasil_at.values() if r["status"] == "DILEWATI")
    total    = len(aturan_list)

    print(f"{'─' * 70}")
    print(f"{BOLD}  RINGKASAN KONSISTENSI:{RESET}")
    print(f"  {GREEN}SESUAI         : {sesuai:>2} / {total}{RESET}")
    print(f"  {RED}TIDAK SESUAI   : {tidak:>2} / {total}{RESET}")
    print(f"  {YELLOW}PERLU CEK MANUAL: {manual:>2} / {total}{RESET}")
    print(f"  {YELLOW}DILEWATI        : {dilewati:>2} / {total}{RESET}")

    if tidak == 0:
        print(f"\n  {GREEN}{BOLD}[OK] Semua konsistensi antar dokumen terverifikasi.{RESET}")
    else:
        print(f"\n  {RED}{BOLD}[!] Ada {tidak} ketidaksesuaian!{RESET}")
        for item in aturan_list:
            if hasil_at[item["kode"]]["status"] == "TIDAK SESUAI":
                print(f"       - ({item['kode']}) {item['nama']}")

    print()


# ── Tampilkan hasil ─────────────────────────────────────────────────────────

def _cetak_item(item: dict, hasil_deteksi: dict) -> None:
    kode    = item["kode"]
    syarat  = item["syarat"]
    catatan = item["catatan"]
    r       = hasil_deteksi[kode]
    status  = r["status"]

    if "LENGKAP" in status:
        warna = GREEN
        ikon  = f"[{status}]"
    elif status == "ADA (nama file)":
        warna = GREEN                 # dihitung hadir, tetap ditandai 'via nama file'
        ikon  = "[ADA - nama file]"
    else:
        warna = RED
        ikon  = "[TIDAK ADA] "

    print(f"{warna}{BOLD}  ({kode}) {syarat}{RESET}")
    print(f"       File   : {r['file']}")
    print(f"       Status : {warna}{ikon}{RESET}")
    print(f"       {r['detail']}")
    if r.get("catatan_kontrak"):
        print(f"       {YELLOW}>> Catatan: hanya tercantum di dokumen acuan (KHS) / "
              f"pasal cara pembayaran kontrak; deliverable belum ditemukan.{RESET}")
    if catatan:
        print(f"       >> {catatan}")
    print()


def tampilkan_hasil(folder: str, syarat_list: list, hasil_deteksi: dict,
                    semua_pdf: dict,
                    diluar_list: list = None,
                    hasil_diluar: dict = None) -> None:
    print(f"{'=' * 70}")
    print(f"{BOLD}{CYAN}  HASIL VERIFIKASI SYARAT BAYAR{RESET}")
    print(f"  Folder  : {folder}")
    print(f"  Tanggal : {datetime.now().strftime('%d %B %Y %H:%M')}")
    print(f"{'=' * 70}\n")

    # ── Bagian 1: Syarat Bayar ───────────────────────────────────────────────
    print(f"{BOLD}{CYAN}  [A] SYARAT BAYAR{RESET}")
    print(f"{'─' * 70}\n")
    for item in syarat_list:
        _cetak_item(item, hasil_deteksi)

    # ── Bagian 2: Kelengkapan Diluar Syarat Bayar SPMK ──────────────────────
    if diluar_list and hasil_diluar:
        print(f"{'─' * 70}")
        print(f"{BOLD}{CYAN}  [B] KELENGKAPAN DILUAR SYARAT BAYAR SPMK{RESET}")
        print(f"{'─' * 70}\n")
        for item in diluar_list:
            _cetak_item(item, hasil_diluar)

    # ── File tidak terdeteksi sebagai syarat apapun ──────────────────────────
    semua_terdeteksi = set()
    for r in hasil_deteksi.values():
        semua_terdeteksi.update(r.get("file_names", [r["file"]]))
    if hasil_diluar:
        for r in hasil_diluar.values():
            semua_terdeteksi.update(r.get("file_names", [r["file"]]))
    semua_terdeteksi.discard("-")

    tidak_termap = [n for n in semua_pdf if n not in semua_terdeteksi]
    if tidak_termap:
        print(f"{'─' * 70}")
        print(f"{BOLD}FILE TIDAK TERDETEKSI SEBAGAI SYARAT (dokumen pendukung lain):{RESET}")
        for nama in tidak_termap:
            info = semua_pdf[nama]
            print(f"  - {nama} ({info['halaman']} hal, {info['ukuran_kb']} KB, {info['sumber']})")
        print()

    # ── Ringkasan Syarat Bayar ───────────────────────────────────────────────
    lengkap   = sum(1 for r in hasil_deteksi.values() if "LENGKAP" in r["status"])
    ada_nama  = sum(1 for r in hasil_deteksi.values() if r["status"] == "ADA (nama file)")
    tidak_ada = sum(1 for r in hasil_deteksi.values() if r["status"] == "TIDAK ADA")
    total     = len(syarat_list)

    print(f"{'=' * 70}")
    print(f"{BOLD}  RINGKASAN SYARAT BAYAR:{RESET}")
    print(f"  {GREEN}LENGKAP  (kata kunci terverifikasi) : {lengkap:>2} / {total}{RESET}")
    print(f"  {GREEN}ADA      (cocok nama file, hadir)   : {ada_nama:>2} / {total}{RESET}")
    print(f"  {RED}TIDAK ADA / tidak ditemukan         : {tidak_ada:>2} / {total}{RESET}")

    if tidak_ada == 0:
        print(f"\n  {GREEN}{BOLD}[OK] Semua syarat bayar hadir.{RESET}")
    else:
        print(f"\n  {RED}{BOLD}[!] Ada {tidak_ada} syarat TIDAK ditemukan!{RESET}")
        for item in syarat_list:
            if hasil_deteksi[item["kode"]]["status"] == "TIDAK ADA":
                print(f"       - ({item['kode']}) {item['syarat']}")

    # ── Ringkasan Diluar Syarat Bayar ────────────────────────────────────────
    if diluar_list and hasil_diluar:
        total_d     = len(diluar_list)
        lengkap_d   = sum(1 for r in hasil_diluar.values() if "LENGKAP" in r["status"])
        ada_nama_d  = sum(1 for r in hasil_diluar.values() if r["status"] == "ADA (nama file)")
        tidak_ada_d = sum(1 for r in hasil_diluar.values() if r["status"] == "TIDAK ADA")

        print(f"\n{'─' * 70}")
        print(f"{BOLD}  RINGKASAN KELENGKAPAN DILUAR SYARAT BAYAR SPMK:{RESET}")
        print(f"  {GREEN}LENGKAP  (kata kunci terverifikasi) : {lengkap_d:>2} / {total_d}{RESET}")
        print(f"  {GREEN}ADA      (cocok nama file, hadir)   : {ada_nama_d:>2} / {total_d}{RESET}")
        print(f"  {RED}TIDAK ADA / tidak ditemukan         : {tidak_ada_d:>2} / {total_d}{RESET}")

        if tidak_ada_d == 0:
            print(f"\n  {GREEN}{BOLD}[OK] Semua kelengkapan diluar syarat bayar hadir.{RESET}")
        else:
            print(f"\n  {RED}{BOLD}[!] Ada {tidak_ada_d} kelengkapan TIDAK ditemukan!{RESET}")
            for item in diluar_list:
                if hasil_diluar[item["kode"]]["status"] == "TIDAK ADA":
                    print(f"       - ({item['kode']}) {item['syarat']}")

    print(f"\n{'=' * 70}")
    print(f"{BOLD}CATATAN:{RESET}")
    print("  - LENGKAP        : kata kunci ditemukan di isi (teks digital / OCR)")
    print("  - ADA (nama file): kata kunci tidak ada di teks, tapi nama file cocok")
    print("                     → buka file dan verifikasi manual isinya")
    print("  - Kuitansi       : pastikan fisik 4 rangkap + 1 asli bermaterai")
    print("  - BASTB          : harus menyebutkan 'di gudang unit pelaksana'")
    print("  - Jaminan Pemeliharaan: surat pernyataan/jaminan untuk masa pemeliharaan")
    print()


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    folder = pilih_folder()
    semua_pdf = baca_semua_pdf(folder)
    # pemenang halaman dinilai lintas SEMUA syarat (daftar lampiran menyebut
    # item dari kedua kelompok), lalu dipakai dua-duanya
    menang = _pemenang_halaman(semua_pdf, SYARAT + DILUAR_SYARAT)
    hasil = deteksi_syarat(semua_pdf, SYARAT, menang)
    hasil_diluar = deteksi_syarat(semua_pdf, DILUAR_SYARAT, menang)
    deteksi_addendum_dalam_kr(semua_pdf, hasil_diluar)
    tampilkan_hasil(folder, SYARAT, hasil, semua_pdf, DILUAR_SYARAT, hasil_diluar)

    # ── Konsistensi antar dokumen ──
    hasil_konsistensi = cek_konsistensi(semua_pdf, hasil, folder)
    tampilkan_konsistensi(folder, ATURAN_KONSISTENSI, hasil_konsistensi)


if __name__ == "__main__":
    main()
