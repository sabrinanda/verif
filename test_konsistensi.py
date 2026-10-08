"""Tests for cross-document consistency checks in verifikasi_syarat_bayar_v2.

Run:  python test_konsistensi.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from verifikasi_syarat_bayar_v2 import (
    terbilang_dari_angka,
    ekstrak_tanggal,
    ekstrak_nilai_rp,
    ekstrak_terbilang,
    ekstrak_no_kontrak,
    ekstrak_kode_barang,
    ekstrak_ref_tanggal_kontrak,
    _token_bilangan,
    _terbilang_match,
    _ekstrak_angka_setelah_label,
    bersihkan_noise_ocr,
    _gabung_per_halaman,
    buang_bagian_cara_pembayaran,
    posisi_pola_teks,
    is_dokumen_acuan,
    is_kontrak_rinci,
    _pemenang_halaman,
    deteksi_syarat,
    deteksi_addendum_dalam_kr,
    _cari_info,
    _cari_file_names,
    SYARAT,
    DILUAR_SYARAT,
    ATURAN_KONSISTENSI,
)


_errors = 0


def check(got, want, label=""):
    global _errors
    if got != want:
        print(f"  FAIL {label}: got {got!r}, want {want!r}")
        _errors += 1
        return False
    print(f"  OK   {label}")
    return True


# ── terbilang_dari_angka ──

def test_terbilang():
    cases = [
        (0, "nol"),
        (1, "satu"),
        (10, "sepuluh"),
        (11, "sebelas"),
        (12, "dua belas"),
        (20, "dua puluh"),
        (21, "dua puluh satu"),
        (100, "seratus"),
        (101, "seratus satu"),
        (110, "seratus sepuluh"),
        (111, "seratus sebelas"),
        (115, "seratus lima belas"),
        (120, "seratus dua puluh"),
        (199, "seratus sembilan puluh sembilan"),
        (200, "dua ratus"),
        (1000, "seribu"),
        (1001, "seribu satu"),
        (1100, "seribu seratus"),
        (2000, "dua ribu"),
        (10_000, "sepuluh ribu"),
        (11_000, "sebelas ribu"),
        (20_000, "dua puluh ribu"),
        (100_000, "seratus ribu"),
        (200_000, "dua ratus ribu"),
        (1_000_000, "satu juta"),
        (1_500_000, "satu juta lima ratus ribu"),
        (44_706_245, "empat puluh empat juta tujuh ratus enam ribu dua ratus empat puluh lima"),
        (1_000_000_000, "satu milyar"),
        (2_500_000_000, "dua milyar lima ratus juta"),
    ]
    for n, expected in cases:
        got = terbilang_dari_angka(n)
        check(got, expected, f"terbilang({n})")


# ── ekstrak_tanggal ──

def test_ekstrak_tanggal():
    def t(teks, exp):
        check(ekstrak_tanggal(teks), exp, f"tanggal({teks[:50]})")

    t("mempawah, 27 januari 2026", ["2026-01-27"])
    t("Jakarta, 15 Maret 2025", ["2025-03-15"])
    t("27-01-2026", ["2026-01-27"])
    t("27/01/2026", ["2026-01-27"])
    t("15 maret 2025", ["2025-03-15"])
    # "juli" berjarak 1 huruf dari "juni" — harus tetap juli (kasus 0114.SPK)
    t("pontianak, 13 juli 2026", ["2026-07-13"])
    t("13 juni 2026", ["2026-06-13"])
    t("tidak ada tanggal", [])
    t("", [])
    # tanggal referensi kontrak ("<no>/2026 tanggal 8 mei 2026") bukan tanggal
    # dokumen — di Faktur 0098.SPK tanggal dokumen hilang kena OCR dan tanggal
    # KR di deskripsi barang ikut terbaca sebagai tanggal faktur
    t("1 010000 f12070000/2026 tanggal 8 mei 2026", [])
    t("kr no. 0098.spk/dan.01.03/f12070000/2026 tanggal 8 mei 2026 "
      "kota pontianak, 08 juli 2026", ["2026-07-08"])


# ── ekstrak_nilai_rp ──

def test_ekstrak_nilai_rp():
    def t(teks, exp, label=""):
        got = ekstrak_nilai_rp(teks)
        check(got, exp, label or f"rp({teks[:50]})")

    t("Rp 44.706.245", 44_706_245)
    t("rp. 44.706.245,00", 44_706_245)
    t("Rp44.706.245", 44_706_245)
    t("sebesar Rp 5.000.000", 5_000_000)
    # anchor version
    got = ekstrak_nilai_rp("uang sejumlah Rp 10.000.000", "uang sejumlah")
    check(got, 10_000_000, "rp anchor")
    got = ekstrak_nilai_rp("pembayaran sebesar 10096 rp. 97,347,657,- x 100 96 - rp. 97,347,657,-", "sebesar")
    check(got, 97_347_657, "rp anchor comma thousands")
    # no match
    t("tidak ada angka", None)
    t("Rp", None)
    # nomor rekening tidak boleh dianggap nilai (kasus 0055.SPK Permohonan)
    t("nilai pembayaran : rp. 63.889.697.,- transfer ke rekening melalui bank "
      "mandiri dengan no. rekening : 146.000.3793796.", 63_889_697,
      "rekening bukan nilai")


# ── _ekstrak_angka_setelah_label ──

def test_ekstrak_angka_setelah_label():
    # normal: nilai setelah label
    check(_ekstrak_angka_setelah_label(
        "jumlah ppn (pajak pertambahan nilai) 7.590.522,00", "jumlah ppn", 600),
        7_590_522, "ppn setelah label")
    # OCR Faktur 0098.SPK: nilai keluar SEBELUM label, dan setelah label hanya
    # ada angka referensi "6815...." yang dulu salah terbaca sebagai PPN
    ocr = ("dasar pengenaan pajak 63.254.353,00\n\n"
           "7.590.522.00\n\n"
           "jumlah ppn (paiak pertamhahan nilai)\n\n"
           "jumlah ppnbm (pajak penjualan atas barang mewah)\n\n"
           "supriatno\n\n"
           "(referensi 6815.0098.spk/dan.01.03/f12070000/2026)")
    check(_ekstrak_angka_setelah_label(ocr, "jumlah ppn", 600),
          7_590_522, "ppn sebelum label (OCR acak)")


# ── ekstrak_terbilang ──

def test_ekstrak_terbilang():
    def t(teks, exp):
        check(ekstrak_terbilang(teks), exp, f"terbilang({teks[:40]})")

    t("terbilang : # empat puluh empat juta # rupiah #",
      "empat puluh empat juta")
    t("terbilang: dua ratus ribu rupiah",
      "dua ratus ribu")
    t("tidak ada", None)
    t("", None)


# ── _terbilang_match ──

def test_terbilang_match():
    def t(teks, nilai, exp):
        check(_terbilang_match(teks, nilai), exp,
              f"terbilang_match({teks[:30]}, {nilai})")

    t("empat puluh empat juta", 44_000_000, True)
    t("empat puluh empat juta", 44_706_245, False)
    t("dua ratus ribu", 200_000, True)
    t("", 200_000, False)
    # ejaan KBBI "miliar" + prefix label bilingual (kasus 0114.SPK Kuitansi)
    t("amount in words : satu miliar dua puluh delapan juta empat ratus "
      "sembilan puluh tujuh ribu seratus tiga belas", 1_028_497_113, True)
    # tanda kutip OCR menempel di kata pertama (kasus 0070.SPK Permohonan)
    t("“empat puluh tiga juta enam ratus delapan puluh empat ribu "
      "enam ratus dua puluh satu", 43_684_621, True)


# ── ekstrak_no_kontrak ──

def test_ekstrak_no_kontrak():
    def t(teks, exp):
        got = ekstrak_no_kontrak(teks)
        if got is not None:
            got = got.strip().lower()[:40]
        check(got, exp, f"no_kontrak({teks[:40]})")

    t("0057.SPK/DAN.01.03/F12070000/2025", "0057.spk/dan.01.03/f12070000/2025")
    t("0050.SPK/DAN.01.03/F12070000/2026", "0050.spk/dan.01.03/f12070000/2026")
    t("nomor: 0310.pj/dan/f1207/2026", "0310.pj/dan/f1207/2026")
    t("nomor: 0057.spk/dan/f1207/2025", "0057.spk/dan/f1207/2025")
    t("Sesuai dengan Surat Kontrak Rinci No. : 0095.SPK/DAN.01.03/F12070000/2026 Tanggal 08 Mei 2026",
      "0095.spk/dan.01.03/f12070000/2026")
    t("nomor kontrak rinci : 0094.spk/dan.01.03/f12070000/2026 08 mei 2026",
      "0094.spk/dan.01.03/f12070000/2026")
    t("tidak ada nomor", None)


# ── ekstrak_kode_barang ──

def test_ekstrak_kode_barang():
    def t(teks, exp):
        check(ekstrak_kode_barang(teks), exp, f"kode barang({teks[:40]})")

    t("kode barang / jasa : 010000", "010000")
    t("kode/jasa: 010000", "010000")
    t("kode barang/jasa : 290000", "290000")
    t("kode barang/jasa nama barang harga\n290000 pemasangan garsip", "290000")
    t("tidak ada kode", None)


# ── bersihkan_noise_ocr ──

def test_bersihkan_noise_ocr():
    noise = "eee tnnnan e aa aa a aa a n aa n n aa aa se ee a nae n an aa n m a an aa a a an a aa aa a n na aa n na an"
    noise_2 = "se n n aae a aa an aa aa g g me ne aa a a aa a aa a aa ma aa a aa a e e e ee e ee aa a kek aa an a ee a aa aa a 1 mae b eee"
    contract = "nomor addendum pertama : 0243.add/dan.01.03/f12070000/2025"
    sample = "pt. pln (persero) up3 mempawah\n" + noise + "\n" + noise_2 + "\n" + contract + "\n"
    cleaned = bersihkan_noise_ocr(sample)
    check(noise not in cleaned, True, "remove repetitive OCR noise")
    check(noise_2 not in cleaned, True, "remove second repetitive OCR noise")
    check(contract in cleaned, True, "preserve Indonesian contract line")
    check("pt. pln (persero) up3 mempawah" in cleaned, True,
          "preserve Indonesian header")
    check(bersihkan_noise_ocr("aa aa"), "aa aa", "preserve short repeated text")
    check(bersihkan_noise_ocr(""), "", "preserve empty text")


def test_ocr_language():
    from verifikasi_syarat_bayar_v2 import OCR_LANG
    check(OCR_LANG, "ind", "use Indonesian OCR language")


# ── halaman: \f pipeline ──

def test_gabung_per_halaman():
    dig = "hal a\f\fhal c"
    ocr = "ocr a\focr b\focr c"
    gab = _gabung_per_halaman(dig, ocr)
    hal = gab.split("\f")
    check(len(hal), 3, "jumlah halaman tetap 3")
    check("hal a" in hal[0] and "ocr a" in hal[0], True, "halaman 1 tergabung")
    check(hal[1].strip(), "ocr b", "halaman digital kosong diisi ocr")
    check(hal[2].strip(), "hal c\nocr c", "halaman 3 tergabung")
    check(_gabung_per_halaman("", "a\fb").split("\f"), ["a", "b"],
          "digital kosong -> ocr apa adanya")


def test_noise_ocr_per_halaman():
    noise = ("eee tnnnan e aa aa a aa a n aa n n aa aa se ee a nae n an aa "
             "n m a an aa a a an a aa aa a n na aa n na an")
    sample = "halaman satu bersih\n" + noise + "\fhalaman dua bersih\n"
    cleaned = bersihkan_noise_ocr(sample)
    check(cleaned.count("\f"), 1, "pembatas \\f dipertahankan")
    check("halaman satu bersih" in cleaned, True, "isi halaman 1 dipertahankan")
    check("halaman dua bersih" in cleaned, True, "isi halaman 2 dipertahankan")
    check(noise not in cleaned, True, "noise di akhir halaman tetap dibuang")


# ── daftar lampiran surat permohonan ──

def test_buang_daftar_lampiran():
    teks = (
        "perihal : mohon pembayaran 100%\n"
        "terlampir kami sampaikan sebagai berikut:\n"
        "a. surat permohonan pembayaran;\n"
        "b. kuitansi / invoice;\n\f"
        "p. asli bermaterai jaminan pemeliharaan;\n"
        "jika pekerjaan tidak mewajibkan slo "
        "maka cukup melampirkan dokumen nidi yang sudah terbit\n"
        "hormat kami\n"
    )
    bersih, dibuang = buang_bagian_cara_pembayaran(teks)
    check("jaminan pemeliharaan" not in bersih, True, "daftar lampiran dibuang")
    check("kuitansi" not in bersih, True, "item daftar sebelum \\f ikut dibuang")
    check("nidi" not in bersih, True, "kalimat penanda nidi ikut dibuang")
    check("hormat kami" in bersih, True, "teks setelah blok dipertahankan")
    check(bersih.count("\f"), 1, "pembatas halaman disisipkan kembali")
    check("jaminan pemeliharaan" in dibuang, True, "blok tercatat di teks_abai")

    b2, _ = buang_bagian_cara_pembayaran(
        "terlampir kami sampaikan laporan progres bulan ini.\nhormat kami\n")
    check("laporan progres" in b2, True, "heading tanpa daftar tak terpotong")


# ── posisi match ──

def test_posisi_pola_teks():
    teks = "permohonan pembayaran lalu kuitansi"
    p, label = posisi_pola_teks(teks, [["kuitansi"]])
    check(p, teks.index("kuitansi"), "posisi kata tunggal")
    check(label, '"kuitansi"', "label pola tunggal")

    p, _ = posisi_pola_teks(
        teks, [["surat permohonan pembayaran"], ["permohonan pembayaran"]])
    check(p, 0, "pola paling awal yang menang")

    p, _ = posisi_pola_teks("format surat pernyataan jaminan",
                            [["surat pernyataan"]])
    check(p, None, "template 'format ...' diabaikan")

    p, _ = posisi_pola_teks("invoice " + "x" * 300 + " pembayaran",
                            [["invoice", "pembayaran"]])
    check(p, None, "multi-kata di luar jendela proximity tidak cocok")

    p, _ = posisi_pola_teks("invoice untuk pembayaran",
                            [["invoice", "pembayaran"]])
    check(p, 0, "multi-kata dalam jendela cocok, posisi = kata pertama")

    p, _ = posisi_pola_teks("nomor perintah kerja : 0208.spmk/dan.01.03/f12070000/2026",
                            [["spmk"]])
    check(p, None, "kata kunci di dalam nomor dokumen diabaikan")
    teks = "no 0208. spmk / dan ... spmk"
    p, _ = posisi_pola_teks(teks, [["spmk"]])
    check(p, teks.rindex("spmk"), "nomor ber-spasi (OCR) diabaikan, sebutan lain tetap cocok")
    p, _ = posisi_pola_teks("3. spmk / surat perintah", [["spmk"]])
    check(p, 3, "penomoran daftar (1-2 digit) bukan nomor dokumen")


def test_nomor_dokumen_pola_multi():
    teks = "0208.spmk/dan pembayaran " + "x" * 300 + " spmk"
    for pola in (["spmk", "pembayaran"], ["pembayaran", "spmk"]):
        p, _ = posisi_pola_teks(teks, [pola])
        check(p, None, f"nomor dokumen bukan bagian pola {pola}")
    p, _ = posisi_pola_teks("spmk untuk pembayaran", [["spmk", "pembayaran"]])
    check(p, 0, "pola multi dengan SPMK asli tetap cocok")
    p, _ = posisi_pola_teks("no 0208.\n spmk / dan", [["spmk"]])
    check(p, None, "nomor dokumen ber-spasi dan newline diabaikan")
    p, _ = posisi_pola_teks("99. spmk / surat perintah", [["spmk"]])
    check(p, 4, "daftar dua digit tetap cocok")


def test_nomor_dokumen_bukan_isi():
    # BAPP 0083 hal 1: no. SPMK di daftar referensi muncul di atas "bapp ... 100%"
    # -> halaman tidak boleh diatribusikan ke (y) SPMK.
    hal = ("10.nomor perintah kerja : 0208.spmk/dan.01.03/f12070000/2026\n"
           "13.surat permohonan penerbitan bapp : 130/kks-um/ix/2026\n"
           "14.laporan kemajuan pekerjaan : 100%\n")
    menang = _pemenang_halaman({"BAPP.pdf": _info(hal, 1)}, SYARAT + DILUAR_SYARAT)
    check(set(menang.get("BAPP.pdf", {})), {"f"}, "BAPP menang, bukan nomor SPMK")


def test_label_kr_di_nama_file():
    # Vendor menempel "KR <no>" di SETIAP file (0083, 0097, 0104): deliverable
    # tetap deliverable; KR murni & bundel addendum tetap acuan.
    check(is_dokumen_acuan("03. Faktur Pajak KR No. 0083.pdf"), False, "faktur ber-label KR")
    check(is_dokumen_acuan("KWITANSI KR 104.pdf"), False, "kwitansi ber-label KR")
    check(is_dokumen_acuan("1. SURAT MOHON PEMBAYARAN KR 0097.pdf"), False, "permohonan ber-label KR")
    check(is_dokumen_acuan("16. KR 0023.PJ.pdf"), True, "KR murni tetap acuan")
    check(is_kontrak_rinci("16. KR 0023.PJ.pdf"), True, "KR murni tetap KR")
    check(is_kontrak_rinci("16.d. Addendum 1 KR PJ 0083.pdf"), True, "bundel addendum KR tetap KR")
    check(is_kontrak_rinci("8. ADD, MOM, EVALUASI, SPMK KR 104.pdf"), True, "bundel ADD+MOM KR tetap KR")
    check(is_dokumen_acuan("2. NPWP, PKP, KONTRAK KHS.pdf"), True, "KHS eksplisit tetap acuan")
    check(is_kontrak_rinci("7. KONTRAK RINCI KR 104.pdf"), True, "kontrak rinci eksplisit")
    for nama in ("03. Faktur Pajak KR No. 0083.pdf", "KWITANSI KR 104.pdf",
                 "1. SURAT MOHON PEMBAYARAN KR 0097.pdf", "6. BAPP KR 0083.pdf"):
        check(is_kontrak_rinci(nama), False, f"deliverable bukan KR: {nama}")
        check(is_dokumen_acuan(nama), False, f"deliverable bukan acuan: {nama}")
    for nama in ("KR.pdf", "16. KR 0023.PJ.pdf", "16.d. Adendum 1 KR PJ 0083.pdf",
                 "8. ADD, MOM, EVALUASI, SPMK KR 104.pdf", "Kontrak_Rinci KR 104.pdf"):
        check(is_kontrak_rinci(nama), True, f"kontrak/bundel tetap KR: {nama}")
        check(is_dokumen_acuan(nama), True, f"kontrak/bundel tetap acuan: {nama}")
    check(is_kontrak_rinci("2. NPWP, PKP, KONTRAK KHS.pdf"), False, "KHS bukan KR")


def test_deliverable_kr_bukan_syarat_kontrak():
    nama = "03. Faktur Pajak KR No. 0083.pdf"
    info = _info("faktur pajak\nkode dan nomor seri faktur pajak", 1)
    info["acuan"] = is_dokumen_acuan(nama)
    hasil = deteksi_syarat({nama: info}, SYARAT)
    check(hasil["c"]["status"], "LENGKAP (OCR)", "faktur ber-label KR tetap deliverable")
    check(hasil["z"]["status"], "TIDAK ADA", "label KR di faktur bukan kontrak rinci")
    hasil = deteksi_syarat({nama: _info("", 1)}, SYARAT)
    check(hasil["c"]["status"], "ADA (nama file)", "faktur kosong tetap dari nama")
    check(hasil["z"]["status"], "TIDAK ADA", "label KR di faktur kosong bukan kontrak")
    for kontrak in ("16. KR 0023.PJ.pdf", "Kontrak Rinci.pdf", "KHS.pdf",
                    "8. ADD, MOM, EVALUASI, SPMK KR 104.pdf"):
        hasil = deteksi_syarat({kontrak: _info("", 1)}, SYARAT)
        check(hasil["z"]["status"], "ADA (nama file)", f"kontrak asli tetap cocok: {kontrak}")


# ── first-match-wins per halaman ──

def _info(teks_bersih, halaman):
    return {
        "teks": teks_bersih, "teks_ocr_bersih": teks_bersih,
        "teks_bersih": teks_bersih, "teks_abai": "",
        "halaman": halaman, "ukuran_kb": 12,
        "ada_teks": True, "sumber": "ocr", "acuan": False,
    }


_BUNDEL = (
    "surat permohonan pembayaran\nterbilang sembilan puluh juta rupiah\n"
    "\f"
    "kuitansi\ntelah terima dari pt pln (persero)\n"
    "\f"
    "faktur pajak\nkode dan nomor seri faktur pajak\n"
)


def test_first_match_wins_satu_halaman():
    hal = (
        "perihal : permohonan pembayaran 100%\n"
        "terlampir berita acara pemeriksaan pekerjaan 100%\n"
        "surat pernyataan jaminan pemeliharaan\n"
    )
    menang = _pemenang_halaman({"Surat.pdf": _info(hal, 1)}, SYARAT)
    check(set(menang.get("Surat.pdf", {}).keys()), {"a"},
          "hanya syarat paling atas yang menang")
    check(menang["Surat.pdf"]["a"][0], 1, "menang di halaman 1")


def test_first_match_wins_bundel():
    menang = _pemenang_halaman({"Bundel.pdf": _info(_BUNDEL, 3)}, SYARAT)
    m = menang["Bundel.pdf"]
    check(m.get("a", (None,))[0], 1, "permohonan menang hal 1")
    check(m.get("b", (None,))[0], 2, "kuitansi menang hal 2")
    check(m.get("c", (None,))[0], 3, "faktur pajak menang hal 3")


def test_deteksi_dengan_halaman():
    hasil = deteksi_syarat({"Bundel.pdf": _info(_BUNDEL, 3)}, SYARAT)
    check(hasil["a"]["status"], "LENGKAP (OCR)", "permohonan LENGKAP")
    check(hasil["b"]["status"], "LENGKAP (OCR)", "kuitansi LENGKAP")
    check("hal 2/3" in hasil["b"]["detail"], True, "detail memuat halaman menang")
    check("hal 3/3" in hasil["c"]["detail"], True, "faktur memuat halaman menang")
    check(hasil["n"]["status"], "TIDAK ADA", "jaminan pemeliharaan tidak ikut")


def test_cari_info_nama_file_berkoma():
    nama = "Permohonan Bayar, Foto Dokumentasi.pdf"
    info = {nama: {"teks": "isi", "teks_ocr_bersih": "isi"}}
    hasil = {"a": {"file": nama, "file_names": [nama]}}
    check(_cari_info(info, hasil, "a"), info[nama],
          "nama file berkoma tetap utuh")


def test_deteksi_tetap_melaporkan_nama_only():
    nama_only = "Permohonan Bayar, Foto Dokumentasi.pdf"
    semua_pdf = {
        "hasil_ocr.pdf": _info("permohonan pembayaran", 1),
        nama_only: _info("foto dokumentasi", 1),
    }
    hasil = deteksi_syarat(semua_pdf, [SYARAT[0]])
    check(nama_only in hasil["a"]["file_names"], True,
          "file nama-only tetap dilaporkan")


def test_acuan_nama_file_tetap_cocok():
    # Bundel acuan (isi tak di-OCR) yang namanya mendeklarasikan deliverable
    # lain harus tetap terdeteksi ADA dari nama file (kasus 0070.SPK).
    info = _info("", 21)
    info["acuan"] = True
    info["ada_teks"] = False
    semua_pdf = {"Kontrak Rinci, Adendum, Evaluasi, MoM.pdf": info}
    hasil = deteksi_syarat(semua_pdf, DILUAR_SYARAT)
    check(hasil["u"]["status"], "ADA (nama file)", "MoM dari nama bundel acuan")
    check(hasil["s"]["status"], "ADA (nama file)", "Evaluasi dari nama bundel acuan")


def test_deteksi_kasus_0020():
    # Judul vendor tanpa kata "jaminan" + halaman menyebut BASTP di bawahnya:
    # (n) harus menang lewat judul paling atas, bukan kalah dari sebutan bastp.
    garansi = _info(
        "surat pernyataan pemeliharaan\n"
        "bersama dengan ini kami menyatakan siap menjamin pemeliharaan selama "
        "30 ( tiga puluh ) hari terhitung sejak tanggal berita acara serah "
        "terima ( bastp ) untuk pekerjaan sebagai berikut :", 1)
    hasil = deteksi_syarat({"SPMK NIDI PERNYATAAN GARANSI.pdf": garansi}, SYARAT)
    check(hasil["n"]["status"], "LENGKAP (OCR)", "pernyataan pemeliharaan terdeteksi")

    # Sebutan "nomor amandemen kontrak rinci : ..." di BA adalah referensi,
    # bukan dokumen addendum — (t) tidak boleh LENGKAP dari isi.
    ba = _info(
        "7. nomor amandemen kontrak rinci : 0033.add/dan.01.03/f12070000/2026\n"
        "8. tanggal amandemen kontrak rinci : 15-04-2026", 1)
    hasil_d = deteksi_syarat({"BASTP BAPP BAP BASTB.pdf": ba}, DILUAR_SYARAT)
    check(hasil_d["t"]["status"], "TIDAK ADA", "sebutan amandemen di BA bukan addendum")

    # Nama file acuan "ADD KHS" = addendum level KHS, bukan deliverable (t)
    add_khs = _info("", 21)
    add_khs["acuan"] = True
    add_khs["ada_teks"] = False
    hasil_d = deteksi_syarat(
        {"1. .II ADD KHS PT.WAHANA PRIMA ANUGERAH 2026.pdf": add_khs},
        DILUAR_SYARAT)
    check(hasil_d["t"]["status"], "TIDAK ADA", "nama ADD KHS (acuan) tak dihitung")


def test_addendum_dalam_kr():
    # Hal 1: kontrak asli, pasal boilerplate menyebut "addendum" di tengah
    # halaman (tanpa nomor). Hal 2: dokumen addendum asli (judul + nomor).
    kr = _info(
        "surat perjanjian kontrak rinci\nnomor : 0020.pj/dan.01.03/f12070000/2026\n"
        + "x" * 400 + "\nperubahan dituangkan dalam addendum yang disepakati\n"
        "\f"
        "pt pln (persero) up3 mempawah\naddendum i kontrak rinci\n"
        "nomor : 0033.add/dan.01.03/f12070000/2026\n", 2)
    kr["acuan"] = True
    semua = {"KONTRAK RINCI.pdf": kr}

    hasil_d = deteksi_syarat(semua, DILUAR_SYARAT)
    deteksi_addendum_dalam_kr(semua, hasil_d)
    check(hasil_d["t"]["status"], "LENGKAP (OCR)", "addendum di bundel KR terdeteksi")
    check("hal 2/2" in hasil_d["t"]["detail"], True, "halaman addendum dilaporkan")

    # Tanpa nomor NNNN.Add -> pasal boilerplate saja, tetap TIDAK ADA
    kr2 = _info("pasal 12\naddendum\nperubahan kontrak dituangkan addendum\n", 1)
    kr2["acuan"] = True
    semua2 = {"KONTRAK RINCI.pdf": kr2}
    hasil_d2 = deteksi_syarat(semua2, DILUAR_SYARAT)
    deteksi_addendum_dalam_kr(semua2, hasil_d2)
    check(hasil_d2["t"]["status"], "TIDAK ADA", "boilerplate tanpa nomor tak dihitung")


def test_konsistensi_berurutan():
    check([item["kode"] for item in ATURAN_KONSISTENSI],
          [f"K{i}" for i in range(1, 9)],
          "kode konsistensi berurutan")


# ── _token_bilangan (K3: terbilang Permohonan = Kuitansi) ──

def test_token_bilangan():
    # Kuitansi 0098.SPK: noise OCR '. ' di depan dan '; . amount in words'
    # di belakang tidak boleh merusak perbandingan
    a = _token_bilangan("tujuh puluh enam juta lima ratus sembilan puluh "
                        "lima ribu dua ratus tujuh\n\npuluh satu")
    b = _token_bilangan(". tujuh puluh enam juta lima ratus sembilan puluh "
                        "lima ribu dua ratus tujuh puluh satu\n; . amount in words")
    check(a, b, "token bilangan tahan noise OCR")
    check(_token_bilangan("satu miliar dua ratus"),
          _token_bilangan("satu milyar dua ratus"), "miliar = milyar")
    # nilai beda harus tetap beda
    check(_token_bilangan("enam juta") == _token_bilangan("tujuh puluh enam juta"),
          False, "nilai beda terdeteksi")


# ── ekstrak_ref_tanggal_kontrak (K8: BASTB vs KR) ──

def test_ekstrak_ref_tanggal_kontrak():
    # BASTB 0098.SPK (teks digital)
    check(ekstrak_ref_tanggal_kontrak(
        "keperluan ( Pemasangan Garsip, Kontrak Rinci No.\n"
        "0098.SPK/DAN.01.03/F12070000/2026, Tanggal 8 Mei 2026))."),
        "2026-05-08", "ref tanggal BASTB")
    # KR halaman 1 hasil OCR ('tangga!' + tahun salah baca)
    check(ekstrak_ref_tanggal_kontrak(
        "nomor pihak pertama 0098.spk/dan.01.03/f1 2070000/2026\n\n"
        "tangga! 08 mei 2025\n\npekerjaan:"),
        "2025-05-08", "ref tanggal KR OCR")
    check(ekstrak_ref_tanggal_kontrak("tidak ada kontrak di sini"),
          None, "tanpa kontrak")


# ── Runner ──

def run_all():
    global _errors
    _errors = 0
    tests = sorted(k for k in globals() if k.startswith("test_"))
    for name in tests:
        print(f"  {name}()")
        globals()[name]()
    return _errors


if __name__ == "__main__":
    err = run_all()
    if err:
        print(f"\n{err} test(s) FAILED")
        sys.exit(1)
    print("\nAll tests PASSED")
