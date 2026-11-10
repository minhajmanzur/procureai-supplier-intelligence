"""End-to-end: the five sample quotations through the whole agent graph."""
from pathlib import Path

import pytest

from app.agents.graph import NoUsableQuotes, run_comparison
from app.bootstrap import bootstrap
from app.schemas import RFQ

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "quotes"


@pytest.fixture(scope="module")
def result():
    bootstrap()
    docs = [{"filename": p.name, "text": p.read_text(encoding="utf-8")} for p in sorted(SAMPLES.glob("*.txt"))]
    return run_comparison(docs, RFQ())


def by_name(result, fragment):
    return next(q for q in result["quotes"] if fragment in q["supplier_name"])


def test_all_quotes_parsed_and_matched(result):
    assert len(result["quotes"]) == 5 and result["rejected"] == []
    assert all(q["intel"]["matched"] for q in result["quotes"])


def test_every_agent_reports(result):
    assert [t["agent"] for t in result["trace"]][:2] == ["intake", "supplier_intel"]
    assert {t["agent"] for t in result["trace"]} == {
        "intake", "supplier_intel", "cost_analyst", "risk_analyst", "pricing_auditor", "recommender", "narrator"
    }


def test_abnormally_low_quote_is_held_not_recommended(result):
    pune = by_name(result, "Pune")
    assert pune["headline_rank"] == 1 and pune["held"]
    assert result["recommendation"]["supplier_name"] != pune["supplier_name"]
    assert result["recommendation"]["held_for_verification"][0]["supplier_name"] == pune["supplier_name"]


def test_hidden_costs_surface_for_the_exw_quote(result):
    shenzhen = by_name(result, "Shenzhen")
    kinds = {c["kind"] for c in shenzhen["cost"]["components"] if c["status"] != "quoted"}
    assert {"tooling", "packaging", "freight", "duty", "financing", "moq"} <= kinds
    assert shenzhen["cost"]["hidden_pct_of_headline"] > 0.25


def test_risk_tracks_supplier_history(result):
    assert by_name(result, "Midwest")["risk"]["p_late"] < 0.2 < by_name(result, "Shenzhen")["risk"]["p_late"]


def test_scores_are_bounded_and_ranked(result):
    eligible = [q for q in result["quotes"] if not q["held"]]
    totals = [q["scores"]["total"] for q in eligible]
    assert totals == sorted(totals, reverse=True)
    assert all(0 <= t <= 100 for t in totals)


def test_no_readable_quotes_raises():
    bootstrap()
    with pytest.raises(NoUsableQuotes):
        run_comparison([{"filename": "note.txt", "text": "We will revert shortly."}], RFQ())


# --- supplier dashboard ----------------------------------------------------


def test_dashboard_overview_and_history():
    from app.analysis.history import overview, supplier_history
    from app.db import Supplier, session_scope
    from app.ml.features import load_orders
    from sqlalchemy import select

    bootstrap()
    with session_scope() as s:
        orders = load_orders(s)
        suppliers = [{"id": x.id, "name": x.name, "country": x.country} for x in s.scalars(select(Supplier))]
    o = overview(orders, suppliers)
    assert o["totals"]["suppliers"] == len(suppliers) and "fastener" in o["categories"]
    assert sum(r["orders"] for r in o["suppliers"]) == o["totals"]["orders"]
    assert all(0 <= r["on_time_rate_12m"] <= 1 for r in o["suppliers"])

    h = supplier_history(orders, suppliers[0]["id"])
    assert 10 <= len(h["quarters"]) <= 14  # three years of quarters, none skipped
    assert sum(q["orders"] for q in h["quarters"]) == next(r["orders"] for r in o["suppliers"] if r["id"] == suppliers[0]["id"])
    assert len(h["recent_orders"]) == 12 and h["prices"]
