"""Score and rank quotes, and explain the recommendation in plain language."""
from __future__ import annotations

from app.analysis.fmt import price
from app.config import settings
from app.schemas import RFQ

UNKNOWN_SUPPLIER_SCORE = 50.0
DEFAULT_DELAY_DAYS = 7.0


def _money(x: float) -> str:
    return f"${x:,.0f}"


def score_quotes(entries: list[dict], rfq: RFQ) -> dict:
    """entries: one dict per quote with keys id, supplier_name, quote, intel, cost, risk, pricing."""
    w = rfq.weights.normalised()

    for e in entries:
        profile = e["intel"]["profile"]
        delay_days = profile.get("avg_days_late_when_late") or DEFAULT_DELAY_DAYS
        e["expected_delay_days"] = round(e["risk"]["p_late"] * delay_days, 1)
        e["delay_cost_usd"] = round(e["risk"]["p_late"] * delay_days * settings.delay_cost_rate_per_day * e["cost"]["goods_usd"], 2)
        e["risk_adjusted_usd"] = round(e["cost"]["landed_usd"] + e["delay_cost_usd"], 2)

    # An abnormally low price (several independent signals) is held for verification:
    # it is scored and shown, but cannot be the recommended award or set the cost
    # benchmark the other quotes are measured against.
    for e in entries:
        e["held"] = e["pricing"]["severity"] == "high" and e["pricing"]["direction"] == "low"
    if all(e["held"] for e in entries):
        for e in entries:
            e["held"] = False
    best_cost = min(e["risk_adjusted_usd"] for e in entries if not e["held"])
    best_lead = min(e["risk"]["lead_days"] for e in entries)

    for e in entries:
        profile, risk = e["intel"]["profile"], e["risk"]
        # 10% more expensive than the best costs 40 points; 25% or more scores zero.
        cost_score = min(100.0, max(0.0, 100 - 400 * (e["risk_adjusted_usd"] / best_cost - 1)))
        delivery_score = 100 * (1 - risk["p_late"])
        if profile.get("orders", 0) > 0:
            quality = 1 - min(profile["avg_defect_rate"] / 0.04, 1)
            reliability_score = 100 * (0.7 * profile["on_time_rate"] + 0.3 * quality)
        else:
            reliability_score = UNKNOWN_SUPPLIER_SCORE
        arrival = risk["lead_days"] + e["expected_delay_days"]
        if rfq.needed_in_days:
            lead_score = 100.0 if arrival <= rfq.needed_in_days else max(0.0, 100 - 5 * (arrival - rfq.needed_in_days))
        else:
            lead_score = 100 * best_lead / risk["lead_days"]
        total = w.cost * cost_score + w.delivery_risk * delivery_score + w.reliability * reliability_score + w.lead_time * lead_score
        e["expected_arrival_days"] = round(arrival, 1)
        e["scores"] = {
            "cost": round(cost_score, 1),
            "delivery_risk": round(delivery_score, 1),
            "reliability": round(reliability_score, 1),
            "lead_time": round(lead_score, 1),
            "total": round(total, 1),
        }
        e["flags"], e["negotiation_levers"] = _flags_and_levers(e, rfq)

    # Eligible quotes are ranked first; held quotes follow.
    ranked = sorted(entries, key=lambda e: (e["held"], -e["scores"]["total"]))
    for i, e in enumerate(ranked, 1):
        e["rank"] = i
    by_headline = sorted(entries, key=lambda e: e["cost"]["headline_usd"])
    for i, e in enumerate(by_headline, 1):
        e["headline_rank"] = i

    return {"ranking": [e["id"] for e in ranked], "recommendation": _recommendation(ranked, rfq)}


def _flags_and_levers(e: dict, rfq: RFQ) -> tuple[list[dict], list[str]]:
    q, cost, risk, pricing, profile = e["quote"], e["cost"], e["risk"], e["pricing"], e["intel"]["profile"]
    flags: list[dict] = []
    levers: list[str] = []

    def flag(severity: str, text: str) -> None:
        flags.append({"severity": severity, "text": text})

    if e["held"]:
        flag("high", "Held for verification: the price is abnormally low on several independent checks, so this quote is not eligible for award until the price and specification are confirmed.")
    if pricing["anomaly"]:
        what = "Unusually low price" if pricing["direction"] == "low" else "Unusually high price"
        advice = " Confirm spec equivalence and quality before relying on it." if pricing["direction"] == "low" else ""
        flag(pricing["severity"], f"{what}: {'; '.join(pricing['signals'])}.{advice}")
    if cost["hidden_pct_of_headline"] > 0.10:
        flag("high" if cost["hidden_pct_of_headline"] > 0.20 else "medium",
             f"{_money(cost['hidden_usd'])} of cost sits outside the headline total (+{cost['hidden_pct_of_headline']:.0%}).")
    if risk["p_late"] >= 0.35:
        flag("high", f"{risk['p_late']:.0%} predicted probability of late delivery.")
    elif risk["p_late"] >= 0.20:
        flag("medium", f"{risk['p_late']:.0%} predicted probability of late delivery.")
    if risk.get("aggressive_promise"):
        flag("medium", risk["aggressive_promise"])
    if rfq.needed_in_days and e["expected_arrival_days"] > rfq.needed_in_days:
        flag("medium", f"Expected arrival in about {e['expected_arrival_days']:.0f} days misses the {rfq.needed_in_days}-day requirement.")
    if not e["intel"]["matched"]:
        flag("medium", "Supplier not found in order history; reliability is unverified.")
    else:
        last, before = profile.get("on_time_rate_last_12m"), profile.get("on_time_rate_before")
        if last is not None and before is not None and last < before - 0.08:
            flag("medium", f"On-time performance is deteriorating: {last:.0%} in the last 12 months vs. {before:.0%} before.")
    if q.price_escalation_clause:
        flag("medium", f"Price is not firm: \"{q.price_escalation_clause}\"")
        levers.append("Ask for a fixed price through delivery, or a cap on the escalation clause.")
    if q.validity_days is not None and q.validity_days < 14:
        flag("low", f"Quote is valid for only {q.validity_days} days.")
    if q.warranty_months is not None and q.warranty_months < 12:
        flag("low", f"Warranty is only {q.warranty_months} months.")
    for c in cost["components"]:
        if c["status"] == "unquantified":
            flag("low", f"{c['label']}: get this priced in writing.")
    missing = [ln["item"] for ln in e.get("lines", []) if not ln["quoted"]]
    if missing:
        flag("medium", f"Did not quote {len(missing)} of {len(e['lines'])} items ({'; '.join(m[:40] for m in missing)}). "
                       f"{_money(cost['gap_usd'])} added at this round's median prices so the quote can be compared.")
    for x in e.get("not_requested", []):
        flag("low", f"Quoted an item that was not requested ({x['description'][:50]}, {_money(x['value_usd'])}); left out of the comparison.")
    for msg in cost["warnings"] + q.parse_warnings:
        flag("low", msg)

    for c in cost["components"]:
        if c["kind"] == "tooling":
            levers.append(f"Ask to waive or amortise the {_money(c['amount'])} tooling charge.")
        elif c["kind"] == "moq":
            levers.append(f"Ask for the MOQ to be lowered to {rfq.quantity} units.")
        elif c["kind"] == "financing" and c["amount"] > 0:
            levers.append(f"Negotiate the {q.advance_payment_pct:g}% advance down; current terms cost about {_money(c['amount'])} in financing.")
        elif c["kind"] in ("handling", "surcharge", "documentation") and c["status"] == "hidden":
            levers.append(f"Challenge the {c['label'].lower()} ({_money(c['amount'])}).")
    if cost["incoterm_used"] in ("EXW", "FCA", "FOB", "FAS"):
        levers.append(f"Request a DAP or DDP price to replace the freight estimate under {cost['incoterm_used']} with a firm number.")
    return flags, levers


def _recommendation(ranked: list[dict], rfq: RFQ) -> dict:
    eligible = [e for e in ranked if not e["held"]]
    top = eligible[0]
    cheapest_headline = min(eligible, key=lambda e: e["cost"]["headline_usd"])
    held = [
        {
            "id": e["id"],
            "supplier_name": e["supplier_name"],
            "score": e["scores"]["total"],
            "risk_adjusted_usd": e["risk_adjusted_usd"],
            "saving_vs_recommended_usd": round(top["risk_adjusted_usd"] - e["risk_adjusted_usd"], 2),
            "p_late": e["risk"]["p_late"],
            "signals": e["pricing"]["signals"],
        }
        for e in ranked
        if e["held"]
    ]
    profile = top["intel"]["profile"]
    reasons = [
        f"Risk-adjusted total cost {_money(top['risk_adjusted_usd'])} for {rfq.quantity:,} units "
        f"({price(top['risk_adjusted_usd'] / rfq.quantity)} per unit all-in)."
        if len(rfq.all_lines()) == 1
        else f"Risk-adjusted total cost {_money(top['risk_adjusted_usd'])} for all {len(rfq.all_lines())} items.",
        f"{top['risk']['p_late']:.0%} predicted chance of late delivery on a {top['risk']['lead_days']}-day lead time.",
    ]
    if profile.get("orders", 0) > 0:
        reasons.append(f"{profile['on_time_rate']:.0%} on-time across {profile['orders']} past orders, {profile['avg_defect_rate']:.1%} average defect rate.")
    if top["cost"]["hidden_usd"] <= 0.02 * top["cost"]["headline_usd"]:
        reasons.append("The quoted total is close to the true landed cost: almost nothing sits outside the headline.")

    insight = None
    if cheapest_headline["id"] != top["id"]:
        c = cheapest_headline
        insight = (
            f"{c['supplier_name']} has the lowest headline price ({_money(c['cost']['headline_usd'])}), but "
            f"{_money(c['cost']['hidden_usd'])} of costs outside the headline and {_money(c['delay_cost_usd'])} of expected delay cost "
            f"bring it to {_money(c['risk_adjusted_usd'])} risk-adjusted, rank {c['rank']} of {len(ranked)}."
        )

    runner_up = None
    if len(eligible) > 1:
        r = eligible[1]
        runner_up = {
            "id": r["id"],
            "supplier_name": r["supplier_name"],
            "score": r["scores"]["total"],
            "cost_difference_usd": round(r["risk_adjusted_usd"] - top["risk_adjusted_usd"], 2),
        }
    margin = top["scores"]["total"] - eligible[1]["scores"]["total"] if len(eligible) > 1 else None
    return {
        "held_for_verification": held,
        "id": top["id"],
        "supplier_name": top["supplier_name"],
        "score": top["scores"]["total"],
        "confidence": None if margin is None else "high" if margin >= 10 else "moderate" if margin >= 4 else "low",
        "margin_over_runner_up": None if margin is None else round(margin, 1),
        "reasons": reasons,
        "headline_vs_reality": insight,
        "runner_up": runner_up,
        "before_signing": [f["text"] for f in top["flags"] if f["severity"] in ("high", "medium")] + top["negotiation_levers"],
    }


def template_narrative(rec: dict, ranked: list[dict], split: dict | None = None) -> str:
    parts = [f"Recommendation: award to {rec['supplier_name']} (score {rec['score']:.0f}/100, {rec['confidence'] or 'n/a'} confidence).", " ".join(rec["reasons"])]
    if rec["headline_vs_reality"]:
        parts.append(rec["headline_vs_reality"])
    if rec["runner_up"]:
        ru = rec["runner_up"]
        diff = ru["cost_difference_usd"]
        parts.append(
            f"Runner-up is {ru['supplier_name']} (score {ru['score']:.0f}), "
            f"{_money(abs(diff))} {'more' if diff >= 0 else 'less'} on a risk-adjusted basis."
        )
    if split:
        plan = "; ".join(f"{sp['supplier_name']} for {', '.join(i[:40] for i in sp['items'])}" for sp in split["suppliers"])
        parts.append(
            f"Split-award option: {plan}. That totals {_money(split['total_usd'])} risk-adjusted, "
            f"{_money(split['saving_vs_recommended_usd'])} less than the recommended single award and {_money(split['saving_usd'])} less than "
            f"the cheapest single award ({split['single_award_supplier']}), at the price of managing {len(split['suppliers'])} suppliers; "
            f"each supplier's fixed charges are already counted."
        )
    for h in rec["held_for_verification"]:
        parts.append(
            f"{h['supplier_name']} is held out of the award because its price is abnormally low: {'; '.join(h['signals'])}. "
            f"If the price and specification are verified it would be {_money(h['saving_vs_recommended_usd'])} cheaper than the "
            f"recommended award on a risk-adjusted basis, with a {h['p_late']:.0%} predicted chance of late delivery."
        )
    return "\n\n".join(parts)
