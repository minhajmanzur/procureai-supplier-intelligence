from pathlib import Path

import pytest

from app.ingestion.parser import parse_quote

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "quotes"


def load(name: str):
    return parse_quote((SAMPLES / name).read_text(encoding="utf-8"), name)


def kinds(quote, basis):
    return sorted(f.kind for f in quote.fees if f.basis == basis)


def test_key_value_layout():
    q = load("01_shenzhen_dynatek.txt")
    assert q.supplier_name == "Shenzhen Dynatek Motion Co. Ltd"
    assert (q.line_items[0].quantity, q.line_items[0].unit_price) == (500, 38.90)
    assert q.quoted_total == 19450.00
    assert q.incoterm == "EXW"
    assert q.lead_time_days == 42
    assert q.advance_payment_pct == 100
    assert q.moq == 600
    assert q.validity_days == 15
    assert q.warranty_months == 12
    assert {f.kind: f.amount for f in q.fees if f.basis == "fixed"} == {"tooling": 1800.0, "packaging": 320.0}
    assert kinds(q, "unquantified") == ["freight", "insurance"]


def test_pipe_table_in_eur_with_fee_row():
    q = load("02_bavaria_antriebstechnik.txt")
    assert q.supplier_name == "Bavaria Antriebstechnik GmbH"
    assert q.currency == "EUR"
    assert len(q.line_items) == 1 and q.line_items[0].unit_price == 44.20
    assert q.quoted_total == 22450.00
    assert q.incoterm == "DAP"
    assert q.lead_time_days == 24
    assert (q.advance_payment_pct, q.net_payment_days) == (0, 60)
    # packaging row is a charge, and "transport ... included" must not become one
    assert [(f.kind, f.amount) for f in q.fees] == [("packaging", 350.0)]


def test_prose_email_all_inclusive():
    q = load("03_midwest_precision.txt")
    assert q.supplier_name == "Midwest Precision Drives Inc."
    assert (q.line_items[0].quantity, q.line_items[0].unit_price) == (500, 47.50)
    assert q.quoted_total == 23750.00
    assert q.incoterm == "DDP"
    assert q.lead_time_days == 14
    assert q.net_payment_days == 30
    assert q.warranty_months == 36
    assert q.fees == []


def test_column_table_with_percent_surcharge_and_escalation():
    q = load("04_anatolia_elektrik.txt")
    assert q.supplier_name == "Anatolia Elektrik Motor A.S."
    assert q.line_items[0].unit_price == 41.00
    assert q.incoterm == "CIF"
    assert q.lead_time_days == 30
    assert (q.advance_payment_pct, q.net_payment_days) == (30, 30)
    assert q.validity_days == 7
    assert "copper" in q.price_escalation_clause
    fees = {f.kind: f for f in q.fees}
    assert fees["handling"].percent == 3.5
    assert fees["documentation"].amount == 250.0


def test_buyer_account_charges_are_unquantified():
    q = load("05_pune_rotodyne.txt")
    assert q.supplier_name == "Pune Rotodyne Pvt Ltd"
    assert q.line_items[0].unit_price == 29.50
    assert q.quoted_total == 14750.00
    assert q.incoterm == "FOB"
    assert q.lead_time_days == 10
    assert q.advance_payment_pct == 50
    assert q.warranty_months == 6
    assert kinds(q, "unquantified") == ["bank", "duty", "freight", "insurance"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Delivery: 3-4 weeks ARO", 28),
        ("Lead time 10 working days", 10),
        ("Ships within 2 weeks", 14),
    ],
)
def test_lead_time_variants(text, expected):
    assert parse_quote(f"Supplier: X\n{text}").lead_time_days == expected


def test_unreadable_document_reports_why():
    q = parse_quote("Thanks for your enquiry, we will revert shortly.")
    assert q.line_items == []
    assert any("unit price" in w for w in q.parse_warnings)


# --- PDFs ------------------------------------------------------------------

PDFS = SAMPLES.parent / "quotes_pdf"
FIELDS = ["supplier_name", "currency", "quoted_total", "lead_time_days", "incoterm", "advance_payment_pct",
          "net_payment_days", "moq", "validity_days", "warranty_months"]


@pytest.mark.parametrize("stem", sorted(p.stem for p in SAMPLES.glob("*.txt")))
def test_pdf_parses_the_same_as_its_text_twin(stem):
    """Each sample PDF carries the same quotation as the .txt file of the same name."""
    from app.ingestion.parser import extract_text

    pdf = parse_quote(extract_text(f"{stem}.pdf", (PDFS / f"{stem}.pdf").read_bytes()))
    txt = load(f"{stem}.txt")
    for field in FIELDS:
        assert getattr(pdf, field) == getattr(txt, field), field
    assert [(li.quantity, li.unit_price) for li in pdf.line_items] == [(li.quantity, li.unit_price) for li in txt.line_items]
    fee = lambda f: (f.kind, f.basis, f.amount, f.percent)
    assert sorted(map(fee, pdf.fees), key=str) == sorted(map(fee, txt.fees), key=str)
    assert bool(pdf.price_escalation_clause) == bool(txt.price_escalation_clause)


def test_table_with_one_cell_per_line():
    text = "Supplier: Acme Drives\nDescription\nQty\nUnit Price\nAmount\nServo motor 400 W\n500\n44.20\n22,100.00\nExport packaging\n1\n350.00\n350.00\n"
    q = parse_quote(text)
    assert [(li.description, li.quantity, li.unit_price) for li in q.line_items] == [("Servo motor 400 W", 500, 44.20)]
    assert [(f.kind, f.amount) for f in q.fees] == [("packaging", 350.0)]


def test_sentence_wrapped_across_lines_is_rejoined():
    text = (
        "Supplier: Acme Drives\nQuantity: 10\nUnit Price: USD 5.00\n"
        "Dispatch within 10 days from PO. Payment 50% advance, balance against bill of lading. Bank charges outside\n"
        "India are borne by the buyer.\n"
    )
    assert [(f.kind, f.basis) for f in parse_quote(text).fees] == [("bank", "unquantified")]
