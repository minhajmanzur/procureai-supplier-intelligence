"""OCR for scanned quotations: image-only PDFs and photos.

The OCR engine (RapidOCR, PP-OCR models on ONNX Runtime, runs locally on CPU) returns
text boxes with coordinates. A quotation is mostly layout, so the boxes are put back
into lines with their column gaps, giving text shaped like a text-layer PDF. The
ordinary parser then reads it; nothing downstream knows the difference.
"""
from __future__ import annotations

import io
import math
from functools import lru_cache
from statistics import median

import numpy as np

MAX_LINE_RATIO = 18     # lines wider than this (width / height) are recognised in pieces
TARGET_SIDE = 2400      # pixels on the long side that pages are rendered / scaled to
LOW_CONFIDENCE = 0.80


class OcrUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _engine():
    try:
        from rapidocr import LangRec, RapidOCR
    except ImportError as exc:  # optional dependency
        raise OcrUnavailable("OCR needs the `rapidocr` package (pip install rapidocr onnxruntime).") from exc
    # The English recognition model keeps the spaces between words; the default
    # (Chinese) model drops them. It is downloaded once, on first use.
    return RapidOCR(params={"Rec.lang_type": LangRec.EN, "Global.log_level": "error"})


def _recognise_piece(img: np.ndarray) -> tuple[str, float]:
    r = _engine()(np.ascontiguousarray(img), use_det=False, use_cls=False)
    if not r.txts:
        return "", 0.0
    return r.txts[0], float(r.scores[0])


def _recognise_wide(crop: np.ndarray) -> tuple[str, float]:
    """A very long line is squeezed into a fixed-width model input and comes out as
    garbage. Cut it at the widest word gap near the middle and read the halves."""
    h, w = crop.shape[:2]
    if w / max(h, 1) <= MAX_LINE_RATIO:
        return _recognise_piece(crop)
    gray = crop.mean(axis=2)
    ink = (gray < np.median(gray) * 0.65).sum(axis=0)
    clean = max(1, round(0.04 * h))
    best, run = None, None
    for x in range(int(w * 0.3), int(w * 0.7)):
        if ink[x] <= clean:
            run = (run[0], x) if run else (x, x)
            if best is None or run[1] - run[0] > best[1] - best[0]:
                best = run
        else:
            run = None
    if best is None:
        return _recognise_piece(crop)
    cut = (best[0] + best[1]) // 2
    (left, s1), (right, s2) = _recognise_wide(crop[:, :cut]), _recognise_wide(crop[:, cut:])
    return f"{left} {right}".strip(), min(s1, s2)


def read_boxes(img: np.ndarray, refine: bool = True) -> list[dict]:
    """Run detection + recognition. Returns boxes as dicts: x0, x1, yc, h, angle, text, score.
    refine: re-read very long lines in pieces (needs a straight image to crop cleanly)."""
    # the flags are passed every time: the engine remembers them from the previous call
    result = _engine()(img, use_det=True, use_cls=True, use_rec=True)
    if result.txts is None or len(result.txts) == 0:
        return []
    boxes = []
    for quad, text, score in zip(result.boxes, result.txts, result.scores):
        quad = np.asarray(quad, dtype=float)  # top-left, top-right, bottom-right, bottom-left
        x0, x1 = quad[:, 0].min(), quad[:, 0].max()
        y0, y1 = quad[:, 1].min(), quad[:, 1].max()
        h = float(np.linalg.norm(quad[3] - quad[0]))
        if refine and (x1 - x0) / max(h, 1) > MAX_LINE_RATIO:
            pad = int(h * 0.1)
            crop = img[max(0, int(y0) - pad) : int(y1) + pad, max(0, int(x0)) : int(x1)]
            retry, retry_score = _recognise_wide(crop)
            if retry:
                text, score = retry, retry_score
        boxes.append(
            {
                "x0": float(x0), "x1": float(x1), "xc": float(quad[:, 0].mean()), "yc": float(quad[:, 1].mean()), "h": h,
                "angle": math.atan2(quad[1][1] - quad[0][1], quad[1][0] - quad[0][0]),
                "text": text.strip(), "score": float(score),
            }
        )
    return [b for b in boxes if b["text"]]


def layout_text(boxes: list[dict]) -> str:
    """Rebuild lines of text from OCR boxes, keeping column gaps as runs of spaces."""
    if not boxes:
        return ""
    # A scan is rarely straight: 1 degree of tilt shifts the right end of a table row by
    # more than a line height. Undo the tilt (estimated from the long boxes) before
    # deciding which boxes share a row.
    long_boxes = [b for b in boxes if b["x1"] - b["x0"] > 4 * b["h"]] or boxes
    tilt = median(b["angle"] for b in long_boxes)
    for b in boxes:
        b["row_y"] = b["yc"] - math.tan(tilt) * b["xc"]

    line_h = median(b["h"] for b in boxes)
    char_w = median((b["x1"] - b["x0"]) / max(len(b["text"]), 1) for b in boxes)
    rows: list[list[dict]] = []
    for b in sorted(boxes, key=lambda b: b["row_y"]):
        if rows and abs(b["row_y"] - median(x["row_y"] for x in rows[-1])) < 0.55 * line_h:
            rows[-1].append(b)
        else:
            rows.append([b])

    lines, prev_y = [], None
    for row in rows:
        row.sort(key=lambda b: b["x0"])
        y = median(b["row_y"] for b in row)
        if prev_y is not None and y - prev_y > 2.2 * line_h:
            lines.append("")  # keep paragraph breaks
        prev_y = y
        text = row[0]["text"]
        for left, right in zip(row, row[1:]):
            gap = (right["x0"] - left["x1"]) / char_w
            text += " " * max(2 if gap > 1.5 else 1, round(gap)) + right["text"]
        lines.append(text)
    return "\n".join(lines)


def _fit(img):
    """Scale a PIL image so its long side is about TARGET_SIDE pixels (never more than 2.5x up)."""
    from PIL import Image

    scale = min(2.5, TARGET_SIDE / max(img.size))
    if abs(scale - 1) > 0.1:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    return img


def _tilt_degrees(boxes: list[dict]) -> float:
    long_boxes = [b for b in boxes if b["x1"] - b["x0"] > 4 * b["h"]]
    return math.degrees(median(b["angle"] for b in long_boxes)) if long_boxes else 0.0


def _ocr_pil(img) -> tuple[str, list[float]]:
    from PIL import Image

    img = _fit(img.convert("RGB"))
    # Pass 1 finds the text and how far the page is tilted; if it is, straighten the
    # page and read it again. Long lines can only be cropped cleanly from a straight page.
    boxes = read_boxes(np.asarray(img), refine=False)
    tilt = _tilt_degrees(boxes)
    if abs(tilt) > 0.2:
        img = img.rotate(tilt, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))
    boxes = read_boxes(np.asarray(img))
    return layout_text(boxes), [b["score"] for b in boxes]


def ocr_document(filename: str, data: bytes) -> tuple[str, dict]:
    """OCR a scanned PDF or an image file. Returns (text, {pages, boxes, mean_confidence, low_confidence_boxes})."""
    from PIL import Image

    pages: list[str] = []
    scores: list[float] = []
    if filename.lower().endswith(".pdf"):
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(data)
        for page in pdf:
            scale = TARGET_SIDE / max(page.get_size())
            text, s = _ocr_pil(page.render(scale=scale).to_pil())
            pages.append(text)
            scores += s
    else:
        text, s = _ocr_pil(Image.open(io.BytesIO(data)))
        pages.append(text)
        scores += s
    info = {
        "pages": len(pages),
        "boxes": len(scores),
        "mean_confidence": round(float(np.mean(scores)), 3) if scores else 0.0,
        "low_confidence_boxes": sum(s < LOW_CONFIDENCE for s in scores),
    }
    return "\n\n".join(pages), info
