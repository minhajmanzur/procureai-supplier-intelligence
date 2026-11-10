"""Turn sample PDFs into realistic scans: image-only PDFs with no text layer.

Run:  python scripts/make_scanned_samples.py      (writes samples/scanned/)

Each page is rendered to a bitmap, then degraded the way an office scanner or a phone
photo would: slight rotation, blur, uneven lighting, sensor noise and JPEG artefacts.
The results exercise the OCR path (app/ingestion/ocr.py).
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
OUT = SAMPLES / "scanned"

# (source PDF, output name, rotation in degrees, blur radius, noise level, JPEG quality)
JOBS = [
    ("fasteners_pdf/01_ningbo_eastbolt.pdf", "01_ningbo_eastbolt_scan.pdf", 0.6, 0.6, 6, 70),
    ("fasteners_pdf/02_rheinland_schraubenwerk.pdf", "02_rheinland_schraubenwerk_scan.pdf", -0.8, 0.5, 5, 75),
    ("fasteners_pdf/03_great_lakes_bolt.pdf", "03_great_lakes_bolt_scan.pdf", 1.2, 0.8, 8, 60),
    ("fasteners_pdf/04_kaohsiung_precision.pdf", "04_kaohsiung_precision_scan.pdf", -0.4, 0.5, 5, 75),
    ("fasteners_pdf/05_ludhiana_forge.pdf", "05_ludhiana_forge_photo.jpg", 1.6, 0.9, 9, 55),  # a phone photo
]


def scan(source: Path, rotation: float, blur: float, noise: float, quality: int, seed: int) -> Image.Image:
    page = pdfium.PdfDocument(str(source))[0]
    img = page.render(scale=150 / 72).to_pil().convert("L")  # 150 dpi greyscale, like a typical office scan
    img = img.rotate(rotation, resample=Image.BICUBIC, expand=True, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(blur))
    rng = np.random.default_rng(seed)
    a = np.asarray(img, dtype=np.float32)
    h, w = a.shape
    shade = 1 - 0.10 * np.linspace(0, 1, w)[None, :] - 0.05 * np.linspace(0, 1, h)[:, None]  # uneven lighting
    a = np.clip(a * shade + rng.normal(0, noise, a.shape), 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, "JPEG", quality=quality)  # compression artefacts
    return Image.open(io.BytesIO(buf.getvalue())).convert("L")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for seed, (src, name, rotation, blur, noise, quality) in enumerate(JOBS):
        img = scan(SAMPLES / src, rotation, blur, noise, quality, seed)
        if name.endswith(".pdf"):
            img.save(OUT / name, "PDF", resolution=150)
        else:
            img.save(OUT / name, "JPEG", quality=quality)
    print(f"Wrote {len(JOBS)} scans to {OUT}")
