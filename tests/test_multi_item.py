"""Multi-item RFQs: line matching, incomplete quotes, unrequested lines and split awards."""
from pathlib import Path

import pytest

from app.agents.graph import NoUsableQuotes, run_comparison
from app.analysis.costing import landed_cost
from app.analysis.matching import match_lines, similarity
from app.bootstrap import bootstrap
from app.ingestion.parser import extract_text
from app.schemas import RFQ, Fee, LineItem, ParsedQuote, RFQLine

FOLDER = Path(__file__).resolve().parent.parent / "samples" / "fastener_kit_pdf"
BOLT = "Hex bolt M10x50, DIN 933, grade 8.8, zinc plated"
NUT = "Hex nut M10, DIN 934, grade 8, zinc plated"
WASHER = "Flat washer M10, DIN 125, zinc plated"
KIT = RFQ(needed_in_days=45, lines=[RFQLine(item=BOLT, quantity=50000), RFQLine(item=NUT, quantity=50000), RFQLine(item=WASHER, quantity=100000)])


# --- matching --------------------------------------------------------------


def test_same_part_in_different_words_matches():
    assert similarity(BOLT, "Hex cap screw M10x50 class 8.8 zinc") > similarity(BOLT, "Hex nut M10 class 8 zinc")
    assert similarity(BOLT, "Hex nut M10 class 8 zinc") == 0  # a nut is never a bolt


def test_lines_matched_regardless_of_order():
    quoted = ["Flat washer M10 DIN 125 zinc plated", "Hex nut M10 DIN 934 grade 8 zinc plated", "Hex bolt M10x50 DIN 933 grade 8.8 zinc plated"]
    assert match_lines([BOLT, NUT, WASHER], quoted) == ([2, 1, 0], [])


def test_missing_and_unrequested_lines():
    quoted = ["Hex cap screw M10x50 class 8.8 zinc", "Hex nut M10 class 8 zinc", "Thread locker, 250 ml bottle"]
    assert match_lines([BOLT, NUT, WASHER], quoted) == ([0, 1, None], [2])


def test_single_item_rfq_accepts_a_single_quoted_line_as_is():
    assert match_lines(["Industrial servo motor 400W"], ["AC drive unit SV-40"]) == ([0], [])


def test_rfq_with_lines_summarises_itself():
    assert KIT.quantity == 200000 and KIT.item == "3 items" and len(KIT.all_lines()) == 3
    assert [ln.item for ln in RFQ(item="X", quantity=5).all_lines()] == ["X"]


# --- costing ---------------------------------------------------------------


def test_unquoted_item_is_filled_at_the_median_and_extras_leave_the_headline():
    quote = ParsedQuote(supplier_name="S", incoterm="DDP", quoted_total=1250.0,
                        line_items=[LineItem(description="bolt", quantity=1000, unit_price=1.0), LineItem(description="glue", quantity=1, unit_price=250.0)])
    lines = [
        {"item": "bolt", "requested_qty": 1000, "quoted": True, "description": "bolt", "quantity": 1000, "unit_price": 1.0},
        {"item": "nut", "requested_qty": 1000, "quoted": False, "fill_usd": 0.2},
    ]
    cost = landed_cost(quote, RFQ(), {"country": "United States", "distance_km": 500.0}, 10, lines=lines, extras_value=250.0)
    assert cost["headline_usd"] == 1000 and cost["gap_usd"] == 200 and cost["landed_usd"] == 1200
    assert cost["coverage"] == {"quoted": 1, "requested": 2}


def test_per_piece_charge_follows_the_quantity_bought():
    fee = Fee(kind="surcharge", basis="fixed", amount=1300.0, evidence="USD 6.50 per 1000 pcs", per_quantity=200000)
    quote = ParsedQuote(supplier_name="S", incoterm="DDP", fees=[fee])
    one_line = [{"item": "bolt", "requested_qty": 50000, "quoted": True, "description": "bolt", "quantity": 50000, "unit_price": 0.1}]
    cost = landed_cost(quote, RFQ(), {"country": "United States", "distance_km": 500.0}, 10, lines=one_line)
    assert next(c["amount"] for c in cost["components"] if c["kind"] == "surcharge") == pytest.approx(325.0)


# --- the whole pipeline on the three-item kit -------------------------------


@pytest.fixture(scope="module")
def result():
    bootstrap()
    docs = [{"filename": p.name, "text": extract_text(p.name, p.read_bytes())} for p in sorted(FOLDER.glob("*.pdf"))]
    return run_comparison(docs, KIT)


def by_name(result, fragment):
    return next(q for q in result["quotes"] if fragment in q["supplier_name"])


def test_each_item_gets_its_own_category_and_median(result):
    assert [ln["category"] for ln in result["lines"]] == ["fastener", "nut", "washer"]
    assert [ln["quotes_received"] for ln in result["lines"]] == [5, 5, 4]


def test_every_quote_is_matched_line_by_line(result):
    mty = by_name(result, "Monterrey")  # lists the items in reverse order
    assert [round(ln["unit_price_usd"], 4) for ln in mty["lines"]] == [0.118, 0.0295, 0.0102]
    rhein = by_name(result, "Rheinland")  # EUR, priced per 100
    assert rhein["lines"][1]["unit_price_usd"] == pytest.approx(0.034 * 1.08)


def test_incomplete_quote_is_made_comparable(result):
    gl = by_name(result, "Great Lakes")
    assert gl["cost"]["coverage"] == {"quoted": 2, "requested": 3}
    assert gl["cost"]["gap_usd"] == pytest.approx(result["lines"][2]["median_unit_price_usd"] * 100000, abs=0.01)
    assert gl["cost"]["headline_usd"] == pytest.approx(8350.0)  # thread locker ($222) taken out
    assert gl["not_requested"][0]["description"].startswith("Thread locker")
    assert any("Did not quote 1 of 3" in f["text"] for f in gl["flags"])


def test_split_award_beats_the_best_single_award(result):
    split = result["split_award"]
    assert split and len(split["suppliers"]) >= 2
    assert len(split["assignment"]) == 3
    assert split["total_usd"] < min(q["risk_adjusted_usd"] for q in result["quotes"] if not q["held"])
    assert split["saving_usd"] == pytest.approx(split["single_award_usd"] - split["total_usd"], abs=0.01)
    assert sum(s["cost_usd"] for s in split["suppliers"]) == pytest.approx(split["total_usd"], abs=0.05)


def test_single_item_runs_have_no_split(result):
    bootstrap()
    folder = FOLDER.parent / "fasteners_pdf"
    docs = [{"filename": p.name, "text": extract_text(p.name, p.read_bytes())} for p in sorted(folder.glob("*.pdf"))]
    single = run_comparison(docs, RFQ(item=BOLT, quantity=50000, needed_in_days=45))
    assert single["split_award"] is None and len(single["lines"]) == 1


def test_quotes_for_something_else_entirely_are_rejected():
    bootstrap()
    servo = FOLDER.parent / "quotes" / "03_midwest_precision.txt"
    with pytest.raises(NoUsableQuotes):
        run_comparison([{"filename": servo.name, "text": servo.read_text(encoding="utf-8")}], KIT)
