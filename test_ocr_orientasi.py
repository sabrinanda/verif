"""Self-check: scan miring 90° + halaman raksasa 72 dpi tetap terbaca OCR.

Jalankan: python test_ocr_orientasi.py
Butuh PDF sampel di attachment/; kalau tidak ada, test di-skip (bukan gagal).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verifikasi_syarat_bayar_v2 as v  # noqa: E402

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attachment", "0066.SPK Faktur Pajak.pdf")
if not os.path.exists(path):
    print(f"SKIP: sampel tidak ada -> {path}")
    raise SystemExit(0)
if not v.OCR_TERSEDIA:
    print(f"SKIP: OCR tidak tersedia ({v.OCR_ERROR})")
    raise SystemExit(0)

import fitz  # noqa: E402
import pytesseract  # noqa: E402

# Halaman 2925x2100 pt (foto 72 dpi, tersimpan miring 90° tanpa flag /Rotate).
doc = fitz.open(path)
img = v._render_ocr(doc[0])
doc.close()
assert max(img.size) <= v.OCR_MAX_PX, f"render terlalu besar: {img.size}"

teks = pytesseract.image_to_string(img, lang=v.OCR_LANG, config=v.OCR_CONFIG).lower()
kunci = ["faktur pajak", "030026002248132", "87.700.592", "dasar pengenaan pajak", "0066.spk"]
hilang = [k for k in kunci if k not in teks]
assert not hilang, f"OCR gagal baca halaman miring, hilang: {hilang}"

print(f"OK: render {img.size[0]}x{img.size[1]}, {len(teks.split())} kata, semua kunci terbaca")
