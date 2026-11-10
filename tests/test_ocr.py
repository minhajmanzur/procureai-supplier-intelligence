"""OCR path: scanned PDFs and photos must parse to the same quotation as the original."""
import math
from pathlib import Path

import pytest

from app.ingestion.ocr import layout_text
from app.ingestion.parser import load_document, parse_quote

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
SCANS = SAMPLES / "scanned"
ORIGINALS = SAMPLES / "fasteners_pdf"


def box(text, x0, y, char_w=10, h=20, tilt_deg=0.0):
    """A fake OCR box; `tilt_deg` slides it down as x grows, like a crooked scan."""
    x1 = x0 + char_w * len(text)
    xc = (x0 + x1) / 2
    return {"text": text, "x0": x0, "x1": x1, "xc": xc, "yc": y + math.tan(math.radians(tilt_deg)) * xc, "h": h,
            "angle": math.radians(tilt_deg), "score": 0.99}


# --- layout reconstruction (no OCR engine needed) ---------------------------


def test_boxes_on_one_row_become_one_line_with_column_gaps():
    text = layout_text([box("50,000", 700, 100), box("Hex bolt M10x50", 100, 101), box("89.00", 900, 99), box("4,450.00", 1100, 100)])
    cells = [c for c in text.split("  ") if c.strip()]
    assert [c.strip() for c in cells] == ["Hex bolt M10x50", "50,000", "89.00", "4,450.00"]
    assert "\n" not in text


def test_rows_stay_separate_and_paragraph_gaps_are_kept():
    text = layout_text([box("Line one", 100, 100), box("Line two", 100, 126), box("After a gap", 100, 260)])
    assert text.split("\n") == ["Line one", "Line two", "", "After a gap"]


def test_tilted_scan_rows_are_not_mixed_up():
    """At 1.5 degrees the right end of a row sits lower than the left end of the next one."""
    rows = [("Hex bolt M10x50 zinc plated", "4,450.00"), ("Hex nut M10 zinc plated", "1,225.00"), ("Flat washer M10 zinc", "890.00")]
    boxes = []
    for i, (name, amount) in enumerate(rows):
        boxes += [box(name, 100, 100 + 30 * i, tilt_deg=1.5), box(amount, 1500, 100 + 30 * i, tilt_deg=1.5)]
    lines = layout_text(boxes).split("\n")
    assert [(ln.split("  ")[0], ln.split()[-1]) for ln in lines] == rows


def test_text_pdf_does_not_go_through_ocr():
    name = "01_ningbo_eastbolt.pdf"
    assert load_document(name, (ORIGINALS / name).read_bytes())["ocr"] is None


# --- real scans (slow: runs the OCR models) ----------------------------------

PAIRS = [
    ("01_ningbo_eastbolt_scan.pdf", "01_ningbo_eastbolt.pdf"),
    ("02_rheinland_schraubenwerk_scan.pdf", "02_rheinland_schraubenwerk.pdf"),
    ("03_great_lakes_bolt_scan.pdf", "03_great_lakes_bolt.pdf"),   # tilted 1.2 degrees, blurred, long prose lines
    ("04_kaohsiung_precision_scan.pdf", "04_kaohsiung_precision.pdf"),
    ("05_ludhiana_forge_photo.jpg", "05_ludhiana_forge.pdf"),      # photo: tilted 1.6 degrees, noisy, low JPEG quality
]
FIELDS = ["supplier_name", "currency", "quoted_total", "lead_time_days", "incoterm", "advance_payment_pct",
          "net_payment_days", "moq", "validity_days"]


@pytest.mark.parametrize("scan,original", PAIRS)
def test_scan_parses_like_its_original(scan, original):
    pytest.importorskip("rapidocr")
    doc = load_document(scan, (SCANS / scan).read_bytes())
    assert doc["ocr"] and doc["ocr"]["mean_confidence"] > 0.9
    got = parse_quote(doc["text"], scan)
    want = parse_quote(load_document(original, (ORIGINALS / original).read_bytes())["text"], original)
    for field in FIELDS:
        assert getattr(got, field) == getattr(want, field), field
    assert [(li.quantity, li.unit_price) for li in got.line_items] == [(li.quantity, li.unit_price) for li in want.line_items]
    money = lambda q: sorted((f.kind, f.amount) for f in q.fees if f.basis == "fixed")
    assert money(got) == money(want)
    assert bool(got.price_escalation_clause) == bool(want.price_escalation_clause)


def test_scanned_set_gives_the_same_recommendation_as_the_originals():
    pytest.importorskip("rapidocr")
    from app.agents.graph import run_comparison
    from app.bootstrap import bootstrap
    from app.schemas import RFQ

    bootstrap()
    rfq = RFQ(item="Hex bolt M10x50, DIN 933, grade 8.8, zinc plated", quantity=50000, needed_in_days=45)
    scanned = run_comparison([load_document(p.name, p.read_bytes()) for p in sorted(SCANS.iterdir())], rfq)
    clean = run_comparison([load_document(p.name, p.read_bytes()) for p in sorted(ORIGINALS.glob("*.pdf"))], rfq)

    assert scanned["rejected"] == [] and len(scanned["quotes"]) == 5
    assert scanned["recommendation"]["supplier_name"] == clean["recommendation"]["supplier_name"]
    assert [q["supplier_name"] for q in scanned["quotes"]] == [q["supplier_name"] for q in clean["quotes"]]
    for s, c in zip(scanned["quotes"], clean["quotes"]):
        assert s["quote"]["parse_method"] == "ocr"
        assert s["cost"]["headline_usd"] == c["cost"]["headline_usd"]
        assert any("Read from a scan by OCR" in f["text"] for f in s["flags"])


def test_blank_image_is_rejected_with_a_reason():
    pytest.importorskip("rapidocr")
    import io

    from PIL import Image

    from app.agents.graph import NoUsableQuotes, run_comparison
    from app.schemas import RFQ

    buf = io.BytesIO()
    Image.new("RGB", (800, 1000), "white").save(buf, "PNG")
    doc = load_document("blank.png", buf.getvalue())
    assert doc["text"] == "" and doc["ocr"]["boxes"] == 0
    with pytest.raises(NoUsableQuotes, match="No readable text found"):
        run_comparison([doc], RFQ())
