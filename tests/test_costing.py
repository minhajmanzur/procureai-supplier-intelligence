import pytest

from app.analysis.costing import landed_cost
from app.analysis.pricing import audit_prices
from app.schemas import RFQ, Fee, LineItem, ParsedQuote

RFQ_500 = RFQ(quantity=500)
FAR = {"country": "China", "distance_km": 10000.0}
HOME = {"country": "United States", "distance_km": 900.0}


def quote(**kw) -> ParsedQuote:
    base = dict(supplier_name="S", line_items=[LineItem(description="motor", quantity=500, unit_price=40.0)], incoterm="DDP")
    return ParsedQuote(**{**base, **kw})


def by_kind(cost):
    return {c["kind"]: c for c in cost["components"]}


def test_ddp_with_no_extras_has_nothing_hidden():
    cost = landed_cost(quote(quoted_total=20000), RFQ_500, HOME, lead_days=14)
    assert cost["landed_usd"] == cost["headline_usd"] == 20000
    assert cost["hidden_usd"] == 0


def test_fees_left_out_of_headline_are_hidden():
    q = quote(quoted_total=20000, fees=[Fee(kind="tooling", basis="fixed", amount=1000, evidence="x")])
    assert by_kind(landed_cost(q, RFQ_500, HOME, 14))["tooling"]["status"] == "hidden"


def test_fees_itemised_in_headline_are_quoted():
    q = quote(quoted_total=21000, fees=[Fee(kind="tooling", basis="fixed", amount=1000, evidence="x")])
    cost = landed_cost(q, RFQ_500, HOME, 14)
    assert by_kind(cost)["tooling"]["status"] == "quoted"
    assert cost["hidden_usd"] == 0


def test_exw_import_estimates_freight_insurance_duty_and_origin():
    cost = landed_cost(quote(incoterm="EXW"), RFQ_500, FAR, 30)
    c = by_kind(cost)
    assert {"origin", "freight", "insurance", "duty"} <= set(c)
    assert c["freight"]["amount"] == pytest.approx(20000 * (0.02 + 0.008 * 10), abs=0.01)
    assert all(c[k]["status"] == "estimated" for k in ("origin", "freight", "insurance", "duty"))


def test_quoted_freight_replaces_the_estimate():
    q = quote(incoterm="FOB", fees=[Fee(kind="freight", basis="fixed", amount=900, evidence="Freight USD 900")])
    c = by_kind(landed_cost(q, RFQ_500, FAR, 30))
    assert c["freight"]["amount"] == 900 and c["freight"]["status"] == "hidden"


def test_domestic_supplier_pays_no_duty():
    assert "duty" not in by_kind(landed_cost(quote(incoterm="DAP"), RFQ_500, HOME, 14))


def test_advance_payment_costs_and_credit_earns():
    advance = by_kind(landed_cost(quote(advance_payment_pct=100), RFQ_500, HOME, 73))["financing"]
    assert advance["amount"] == pytest.approx(20000 * 0.10 * 73 / 365, abs=0.01)
    credit = by_kind(landed_cost(quote(net_payment_days=73), RFQ_500, HOME, 14))["financing"]
    assert credit["amount"] == pytest.approx(-400, abs=0.01) and credit["status"] == "benefit"


def test_moq_above_request_counts_half_the_surplus():
    c = by_kind(landed_cost(quote(moq=600), RFQ_500, HOME, 14))
    assert c["moq"]["amount"] == pytest.approx(100 * 40 * 0.5)


def test_currency_conversion():
    cost = landed_cost(quote(currency="EUR", quoted_total=20000), RFQ_500, HOME, 14)
    assert cost["headline_usd"] == pytest.approx(21600)


def test_price_far_below_peers_and_own_history_is_high_severity():
    prices = {"a": 47.0, "b": 48.0, "c": 41.0, "d": 39.0, "e": 29.5}
    out = audit_prices(prices, {"orders": 100, "p05": 38.0, "median": 46.0, "p95": 55.0}, {"e": 43.0})
    assert out["e"]["severity"] == "high" and out["e"]["direction"] == "low"
    assert not any(out[k]["anomaly"] for k in "abcd")
