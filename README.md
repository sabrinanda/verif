# Verifikasi Kelengkapan Syarat Bayar – PLN

Tool Python untuk **mengecek otomatis kelengkapan dokumen syarat bayar** pada
folder tagihan SPK/PJ. Script membaca seluruh PDF dalam satu folder, mengekstrak
isinya (teks digital maupun hasil OCR untuk dokumen hasil scan), lalu mencocokkan
isi dokumen dengan daftar syarat bayar yang sudah ditentukan.

Tujuannya menggantikan pengecekan manual satu per satu agar lebih cepat dan
mengurangi dokumen yang terlewat sebelum tagihan diajukan.

## File utama

| File | Fungsi |
|------|--------|
| `verifikasi_syarat_bayar_v2.py` | **Script utama.** Verifikasi kelengkapan syarat bayar berbasis isi dokumen + OCR. |
| `cek_halaman.py` | Utilitas bantu: menghitung jumlah halaman & gambar tiap PDF di beberapa folder (cek beban OCR). |
| `MONITORING_DOKUMEN.xlsx` | Lembar monitoring dokumen tagihan. |
| `0047.SPK/`, `0048.SPK/`, … | Folder dokumen tagihan per kontrak (SPK/PJ), berisi PDF yang diverifikasi. |
| `.ocr_cache/` (di tiap folder) | Cache hasil OCR agar run berikutnya instan (dibuat otomatis). |

> Catatan: folder SPK juga berisi beberapa versi script lama
> (`verifikasi_syarat_bayar.py`, `check_*.py`, `scan_isi.py`). Versi yang dipakai
> dan dirawat adalah **`verifikasi_syarat_bayar_v2.py`** di root project.

## Cara pakai

```bash
# tanya folder secara interaktif
python verifikasi_syarat_bayar_v2.py

# langsung proses folder tertentu
python verifikasi_syarat_bayar_v2.py "D:/PLN/2026/Tagihan/0057.SPK"
```

Output ditampilkan di terminal: status tiap syarat (berwarna), daftar file yang
tidak terdeteksi sebagai syarat, dan ringkasan jumlah LENGKAP / ADA / TIDAK ADA.

## Cara kerja deteksi

1. **Ekstraksi teks** – tiap PDF dibaca dengan `pdfplumber` (teks digital).
2. **OCR otomatis** – PDF hasil scan (tanpa teks digital), teks rusak
   "huruf dobel" (`addendum → aaddddeenndduumm`), atau yang memuat halaman gambar
   di-OCR dengan **Tesseract** (offline, bahasa Indonesia, 300 dpi). Halaman
   scan yang tersimpan miring 90°/180° dibaca otomatis (`--psm 1`), dan foto
   berukuran halaman raksasa dirender maks 3508 px (≈A4 300 dpi) supaya OCR
   tidak lambat/rusak. Hasil OCR di-cache per folder di `.ocr_cache/`.
3. **Buang pasal "cara pembayaran"** – kontrak (Kontrak Rinci/KHS/SPMK) memuat
   pasal yang **menyebut seluruh syarat bayar**. Daftar ini bukan deliverable;
   bila ikut dicocokkan, kontrak akan salah terdeteksi sebagai semua syarat
   (false positive). Blok ini dibuang sebelum pencocokan.
4. **Pencocokan** – setiap syarat dicocokkan lewat:
   - **pola teks** (kata kunci isi dokumen, dengan cek kedekatan/proximity &
     batas kata agar kata kunci pendek tidak salah cocok di sampah OCR),
   - **template/contoh diabaikan** (judul yang diawali `format`/`contoh` adalah
     template kosong di kontrak, bukan dokumen asli),
   - **nama file** sebagai fallback bila isi tidak cocok.

## Status hasil

| Status | Arti |
|--------|------|
| `LENGKAP` / `LENGKAP (OCR)` | Kata kunci ditemukan di isi dokumen (teks digital / OCR). |
| `ADA (nama file)` | Kata kunci tidak ada di isi, tapi nama file cocok → **verifikasi manual**. |
| `TIDAK ADA` | Tidak ada file yang cocok (isi maupun nama). Bila hanya disebut di pasal cara pembayaran kontrak, diberi catatan. |

## Daftar syarat yang dicek

**[A] Syarat Bayar (a–q):** Surat Permohonan Pembayaran, Kuitansi/Invoice,
Faktur Pajak PPN, NPWP, SK Pengukuhan PKP, BAPP 100%, BASTB, BASTP, LPJM,
Foto dokumentasi, As Built Drawing, Final Evaluasi CSMS, Rapor Penilaian Kinerja
Vendor, Jaminan Pemeliharaan 5%, Bukti BPJS Ketenagakerjaan, Surat Pernyataan
Kebenaran Pemasangan/Penggunaan Tanah Gardu, Dokumen RLB/NIDI.

**[B] Kelengkapan di luar Syarat Bayar SPMK (r–x):** Bobot Fisik, Evaluasi
tambah/kurang, Addendum, MoM, BA Mapping/Tikor, BA IPMS, SBUJK.

> Definisi syarat (kata kunci, hint nama file, catatan) didefinisikan dalam
> list `SYARAT` dan `DILUAR_SYARAT` di awal script — mudah ditambah/diubah.

## Kebutuhan / dependensi

- **Python 3** (memakai `str.reconfigure`, jadi Python 3.7+)
- `pdfplumber` – ekstraksi teks PDF digital
- `PyMuPDF` (`fitz`) – render halaman PDF jadi gambar untuk OCR
- `pytesseract` + `Pillow` – OCR
- **Tesseract-OCR v5.5.3** terpasang di `C:\Program Files\Tesseract-OCR\tesseract.exe`
  dengan data bahasa Indonesia (`ind`). Path lain: set env `TESSERACT_CMD`.
  Versi engine ikut masuk stamp `.ocr_cache`, jadi upgrade Tesseract otomatis
  meng-OCR ulang (hasil lama tidak dipakai diam-diam).

```bash
pip install pdfplumber pymupdf pytesseract pillow
```

> Jika Tesseract tidak terpasang, script tetap jalan tetapi PDF scan hanya
> dicek lewat nama file (ditandai untuk verifikasi manual).
