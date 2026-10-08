"""Self-check: pdfminer FontBBox warning dibungkam, ekstraksi teks tetap utuh.

Jalankan: python _repro_fontbbox.py
Butuh PDF sampel di FOLDER; kalau tidak ada, test di-skip (bukan gagal).
"""
import logging
import os

FOLDER = r"D:\Download\PLN Vendor Docs\0085.Pj_DAN.01.03_F12070000_2026"
SAMPEL = "BASTB, FINAL CSMS, RAPORT VENDOR DAN LAHAN GARDU.pdf"

path = os.path.join(FOLDER, SAMPEL)
if not os.path.exists(path):
    print(f"SKIP: sampel tidak ada -> {path}")
    raise SystemExit(0)

seen = []


class Catch(logging.Handler):
    def emit(self, rec):
        seen.append(rec.getMessage())


logging.getLogger().addHandler(Catch())
logging.getLogger().setLevel(logging.WARNING)

# Impor modul utama menerapkan peredam logging pdfminer.
import verifikasi_syarat_bayar_v2  # noqa: F401,E402
import pdfplumber  # noqa: E402

with pdfplumber.open(path) as pdf:
    teks = "".join((pg.extract_text() or "") for pg in pdf.pages)

bbox_warn = [m for m in seen if "FontBBox" in m]
assert not bbox_warn, f"warning FontBBox masih muncul: {len(bbox_warn)}x"
assert len(teks) > 4000, f"ekstraksi teks rusak: hanya {len(teks)} karakter"

# ERROR pdfminer harus tetap lolos (jangan sampai ikut terbungkam).
seen.clear()
logging.getLogger("pdfminer.pdffont").error("uji error harus tampil")
assert seen, "level ERROR pdfminer ikut terbungkam - peredam kelewat lebar"

print(f"OK: 0 warning FontBBox, {len(teks)} karakter terekstrak, ERROR tetap lolos")
