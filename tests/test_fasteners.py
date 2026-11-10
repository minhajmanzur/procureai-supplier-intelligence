"""Industrial fasteners sample set: per-100 / per-1000 pricing and the full pipeline."""
from pathlib import Path

import pytest

from app.agents.graph import run_comparison
from app.bootstrap import bootstrap
from app.ingestion.parser import extract_text, parse_quote
from app.schemas import RFQ

FOLDER = Path(__file__).resolve().parent.parent / "samples" / "fasteners_pdf"
RFQ_BOLTS = RFQ(item="Hex bolt M10x50, DIN 933, grade 8.8, zinc plated", quantity=50000, needed_in_days=45)


def load(name: str):
    return parse_quote(extract_text(name, (FOLDER / name).read_bytes()), name)


@pytest.mark.parametrize(
    "name,unit_price,total",
    [
        ("01_ningbo_eastbolt.pdf", 0.089, 4450.0),        # "Price per 1000 pcs: USD 89.00"
        ("02_rheinland_schraubenwerk.pdf", 0.124, 6200.0),  # table column "Price per 100"
        ("03_great_lakes_bolt.pdf", 0.129, 6450.0),       # "$0.129 each"
        ("04_kaohsiung_precision.pdf", 0.1045, 5225.0),   # table column "USD / 1000 pcs"
        ("05_ludhiana_forge.pdf", 0.068, 3400.0),         # table column "Rate per 1000 (USD)"
    ],
)
def test_price_basis_is_normalised_to_per_piece(name, unit_price, total):
    q = load(name)
    assert q.line_items[0].quantity == 50000
    assert q.line_items[0].unit_price == pytest.approx(unit_price)
    assert q.quoted_total == total
    assert q.lead_time_days is not None and q.incoterm is not None


def test_per_thousand_surcharge_scales_with_quantity():
    fees = {f.kind: f.amount for f in load("01_ningbo_eastbolt.pdf").fees}
    assert fees == {"surcharge": 325.0, "documentation": 95.0, "packaging": 180.0}  # 6.50 x 50


def test_advance_payment_with_words_in_between():
    assert load("01_ningbo_eastbolt.pdf").advance_payment_pct == 100  # "100% T/T in advance"


def test_free_certificate_is_not_a_charge():
    assert load("02_rheinland_schraubenwerk.pdf").fees == []


def test_clause_wrapped_across_pdf_lines_is_read():
    q = load("05_ludhiana_forge.pdf")
    assert q.lead_time_days == 15 and "steel" in q.price_escalation_clause


@pytest.fixture(scope="module")
def result():
    bootstrap()
    docs = [{"filename": p.name, "text": extract_text(p.name, p.read_bytes())} for p in sorted(FOLDER.glob("*.pdf"))]
    return run_comparison(docs, RFQ_BOLTS)


def test_category_comes_from_the_quotes(result):
    assert result["category"] == "fastener" and result["market"] is not None


def test_fastener_pipeline_outcome(result):
    quotes = {q["supplier_name"].split()[0]: q for q in result["quotes"]}
    assert len(quotes) == 5 and result["rejected"] == []
    assert all(q["intel"]["matched"] for q in quotes.values())
    assert quotes["Ludhiana"]["held"] and quotes["Ludhiana"]["headline_rank"] == 1
    assert result["recommendation"]["supplier_name"] != quotes["Ludhiana"]["supplier_name"]
    assert quotes["Ningbo"]["cost"]["hidden_pct_of_headline"] > 0.25
    assert not quotes["Rheinland"]["pricing"]["anomaly"]  # dearer than peers, but normal for this supplier


def test_default_rfq_text_does_not_override_what_was_quoted():
    """Uploading bolt quotes while the form still says 'servo motor' must still find fasteners."""
    bootstrap()
    docs = [{"filename": p.name, "text": extract_text(p.name, p.read_bytes())} for p in sorted(FOLDER.glob("*.pdf"))]
    assert run_comparison(docs, RFQ(quantity=50000))["category"] == "fastener"
