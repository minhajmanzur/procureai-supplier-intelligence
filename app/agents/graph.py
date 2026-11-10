"""The procurement agent team, orchestrated with LangGraph.

    intake -> supplier_intel -+-> cost_analyst    -+
                              +-> risk_analyst    -+-> recommender -> narrator
                              +-> pricing_auditor -+

The three analysts run in parallel on the same parsed quotes; each owns one part of
the shared state. Every agent appends to `trace` so a run can be audited step by step.
"""
from __future__ import annotations

import operator
import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from app.agents import llm
from app.analysis.costing import landed_cost
from app.analysis.matching import match_lines
from app.analysis.pricing import audit_prices, market_reference
from app.analysis.recommend import score_quotes, template_narrative
from app.analysis.split import best_split
from app.config import settings
from app.db import Supplier, session_scope
from app.ingestion.parser import is_usable, parse_quote
from app.ml import risk as risk_model
from app.ml.features import infer_shipping_mode, load_orders, scoring_row, supplier_profile
from app.schemas import RFQ, ParsedQuote

CATEGORY_KEYWORDS = {
    "nut": r"\bnuts?\b",
    "washer": r"\bwashers?\b",
    "fastener": r"\bbolts?\b|\bscrews?\b|fastener|\bstuds?\b|rivet",
    "servo_motor": r"servo|motor",
    "gearbox": r"gearbox|gear ?reducer|reducer",
    "bearing": r"bearing",
    "controller": r"controller|drive unit|\bplc\b",
}
_LEGAL_SUFFIX = re.compile(r"\b(co|ltd|inc|gmbh|pvt|llc|corp|sa|de|cv|spa|kk|jsc|a\.?s|limited|company)\b\.?", re.I)


class State(TypedDict, total=False):
    rfq: dict
    documents: list[dict]          # {"filename", "text"}
    quotes: dict[str, dict]        # quote id -> ParsedQuote
    rfq_lines: list[dict]          # requested items, with category and this round's median price
    lines: dict[str, list[dict]]   # quote id -> the RFQ lines as matched to that quote
    extras: dict[str, list[dict]]  # quote id -> quoted lines nobody asked for
    rejected: list[dict]
    category: str | None
    intel: dict[str, dict]
    costs: dict[str, dict]
    risks: dict[str, dict]
    pricing: dict[str, dict]
    market: dict | None
    result: dict
    trace: Annotated[list[dict], operator.add]


def _step(agent: str, summary: str) -> list[dict]:
    return [{"agent": agent, "summary": summary}]


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", _LEGAL_SUFFIX.sub("", name.lower())).strip()


def _category(text: str) -> str | None:
    return next((c for c, rx in CATEGORY_KEYWORDS.items() if re.search(rx, text, re.I)), None)


def _quoted_totals(lines: list[dict]) -> tuple[float, float]:
    """(total quantity, total value in base currency) of the requested items this quote covers."""
    quoted = [ln for ln in lines if ln["quoted"]]
    return sum(ln["quantity"] for ln in quoted), sum(ln["quantity"] * ln["unit_price_usd"] for ln in quoted)


def _orders():
    with session_scope() as s:
        return load_orders(s)


# --- agents ----------------------------------------------------------------


def intake(state: State) -> dict:
    """Read each document into a structured quote; fall back to the LLM if rules fail."""
    quotes, rejected = {}, []
    for doc in state["documents"]:
        ocr = doc.get("ocr")
        if not doc["text"].strip():
            reason = ocr["error"] if ocr and ocr.get("error") else "No readable text found, even with OCR." if ocr else "The file is empty."
            rejected.append({"file": doc["filename"], "reason": reason})
            continue
        quote = parse_quote(doc["text"], doc["filename"])
        if not is_usable(quote):
            quote = llm.extract_quote(doc["text"], doc["filename"]) or quote
        if ocr:
            # Scans are read by OCR: say so, and say how sure the reader was.
            quote.parse_method = "ocr" if quote.parse_method == "rules" else "ocr+llm"
            note = f"Read from a scan by OCR (confidence {ocr['mean_confidence']:.0%}"
            if ocr["low_confidence_boxes"]:
                note += f", {ocr['low_confidence_boxes']} uncertain text regions"
            quote.parse_warnings.append(note + "); check the figures against the original.")
        if is_usable(quote):
            quotes[f"q{len(quotes) + 1}"] = quote.model_dump()
        else:
            rejected.append({"file": doc["filename"], "reason": "; ".join(quote.parse_warnings) or "No supplier or pricing found."})

    rfq = RFQ(**state["rfq"])
    rfq_lines = [line.model_dump() for line in rfq.all_lines()]
    matched, extras = {}, {}
    for qid, q in list(quotes.items()):
        fx = settings.fx_rates.get(q["currency"], 1.0)
        items = q["line_items"]
        assignment, extra_idx = match_lines([ln["item"] for ln in rfq_lines], [it["description"] for it in items])
        if all(j is None for j in assignment):
            rejected.append({"file": q["source_file"], "reason": "None of the quoted items match the request."})
            del quotes[qid]
            continue
        matched[qid] = []
        for i, (ln, j) in enumerate(zip(rfq_lines, assignment)):
            row = {"rfq_index": i, "item": ln["item"], "requested_qty": ln["quantity"], "quoted": j is not None}
            if j is not None:
                row.update(description=items[j]["description"], quantity=items[j]["quantity"],
                           unit_price=items[j]["unit_price"], unit_price_usd=items[j]["unit_price"] * fx)
            matched[qid].append(row)
        extras[qid] = [
            {"description": items[j]["description"], "value": items[j]["quantity"] * items[j]["unit_price"],
             "value_usd": items[j]["quantity"] * items[j]["unit_price"] * fx}
            for j in extra_idx
        ]

    for i, ln in enumerate(rfq_lines):
        offers = [m[i] for m in matched.values() if m[i]["quoted"]]
        if not ln["category"]:
            # What the suppliers actually quoted decides the category; the RFQ text is the fallback.
            for haystack in (" ".join(o["description"] for o in offers), ln["item"]):
                ln["category"] = _category(haystack)
                if ln["category"]:
                    break
        prices = sorted(o["unit_price_usd"] for o in offers)
        ln["quotes_received"] = len(prices)
        ln["median_unit_price_usd"] = (prices[len(prices) // 2] + prices[(len(prices) - 1) // 2]) / 2 if prices else None
        for m in matched.values():
            if not m[i]["quoted"]:
                m[i]["fill_usd"] = ln["median_unit_price_usd"]

    category = rfq_lines[0]["category"]
    by_llm = sum("llm" in q["parse_method"] for q in quotes.values())
    by_ocr = sum("ocr" in q["parse_method"] for q in quotes.values())
    incomplete = sum(any(not r["quoted"] for r in m) for m in matched.values())
    summary = f"Parsed {len(quotes)} of {len(state['documents'])} quotations ({by_ocr} by OCR, {by_llm} via LLM fallback)"
    if len(rfq_lines) > 1:
        summary += f"; matched them to {len(rfq_lines)} requested items, {incomplete} incomplete"
    else:
        summary += f"; category '{category}'"
    return {
        "quotes": quotes,
        "rejected": rejected,
        "category": category,
        "rfq_lines": rfq_lines,
        "lines": matched,
        "extras": extras,
        "trace": _step("intake", summary + "."),
    }


def supplier_intel(state: State) -> dict:
    """Match each quote to a known supplier and summarise its delivery history."""
    orders = _orders()
    with session_scope() as s:
        suppliers = [
            {"id": x.id, "name": x.name, "country": x.country, "distance_km": x.distance_km, "years_active": x.years_active, "certifications": x.certifications}
            for x in s.scalars(select(Supplier))
        ]
    intel = {}
    for qid, q in state["quotes"].items():
        scored = [(SequenceMatcher(None, _norm(q["supplier_name"]), _norm(sp["name"])).ratio(), sp) for sp in suppliers]
        score, match = max(scored, key=lambda t: t[0], default=(0.0, None))
        if match and score >= 0.8:
            intel[qid] = {"matched": True, "supplier": match, "profile": supplier_profile(orders, match["id"], state["category"])}
        else:
            intel[qid] = {"matched": False, "supplier": None, "profile": {"orders": 0}}
    known = sum(i["matched"] for i in intel.values())
    return {"intel": intel, "trace": _step("supplier_intel", f"Matched {known} of {len(intel)} suppliers to order history.")}


def _lead_days(quote: dict, profile: dict) -> int:
    return int(quote["lead_time_days"] or profile.get("median_actual_lead_days") or 30)


def cost_analyst(state: State) -> dict:
    """Build the landed cost of every quote and expose what the headline leaves out."""
    rfq = RFQ(**state["rfq"])
    costs = {}
    for qid, q in state["quotes"].items():
        info = state["intel"][qid]
        costs[qid] = landed_cost(
            ParsedQuote(**q), rfq, info["supplier"], _lead_days(q, info["profile"]),
            lines=state["lines"][qid], extras_value=sum(x["value"] for x in state["extras"][qid]),
        )
    worst = max(costs.values(), key=lambda c: c["hidden_usd"])
    return {"costs": costs, "trace": _step("cost_analyst", f"Landed costs built; largest gap between headline and landed cost is ${worst['hidden_usd']:,.0f}.")}


def risk_analyst(state: State) -> dict:
    """Predict the probability of late delivery for each quote with the XGBoost model."""
    risks = {}
    for qid, q in state["quotes"].items():
        info = state["intel"][qid]
        supplier, profile = info["supplier"], info["profile"]
        lead = _lead_days(q, profile)
        quantity, value = _quoted_totals(state["lines"][qid])
        distance = supplier["distance_km"] if supplier else None
        row = scoring_row(
            promised_lead_days=lead,
            quantity=quantity,
            order_value_usd=value,
            distance_km=distance,
            years_active=supplier["years_active"] if supplier else None,
            profile=profile,
        )
        pred = risk_model.predict_late_probability(row)
        mode = infer_shipping_mode(distance, lead)
        typical = profile.get("median_actual_lead_days_by_mode", {}).get(mode)
        aggressive = None
        if typical and lead < 0.75 * typical:
            aggressive = f"Promised {lead} days, but this supplier's typical actual lead time by {mode} is {typical:g} days."
        risks[qid] = {
            **pred,
            "lead_days": lead,
            "lead_time_stated": q["lead_time_days"] is not None,
            "shipping_mode": mode,
            "typical_lead_days": typical,
            "aggressive_promise": aggressive,
        }
    riskiest = max(risks, key=lambda k: risks[k]["p_late"])
    return {
        "risks": risks,
        "trace": _step("risk_analyst", f"Late-delivery risk scored; highest is {state['quotes'][riskiest]['supplier_name']} at {risks[riskiest]['p_late']:.0%}."),
    }


def pricing_auditor(state: State) -> dict:
    """Check every quoted unit price against peers, the market and the supplier's own history."""
    orders = _orders()
    multi = len(state["rfq_lines"]) > 1
    per_line: dict[str, list[dict]] = {qid: [] for qid in state["quotes"]}
    markets = []
    for i, ln in enumerate(state["rfq_lines"]):
        market = market_reference(orders, ln["category"])
        markets.append(market)
        prices, own = {}, {}
        for qid, rows in state["lines"].items():
            if not rows[i]["quoted"]:
                continue
            prices[qid] = rows[i]["unit_price_usd"]
            supplier = state["intel"][qid]["supplier"]
            own[qid] = supplier_profile(orders, supplier["id"], ln["category"]).get("category_median_price_usd_12m") if supplier else None
        for qid, finding in audit_prices(prices, market, own).items():
            value = state["lines"][qid][i]["quantity"] * state["lines"][qid][i]["unit_price_usd"]
            per_line[qid].append({"rfq_index": i, "item": ln["item"], "value_usd": value, **finding})

    pricing = {}
    for qid, findings in per_line.items():
        flagged = [f for f in findings if f["anomaly"]]
        goods = sum(f["value_usd"] for f in findings) or 1.0
        # A quote is "abnormally low" overall only when the suspect lines carry real weight.
        low_share = sum(f["value_usd"] for f in flagged if f["severity"] == "high" and f["direction"] == "low") / goods
        biggest = max(flagged, key=lambda f: f["value_usd"], default=None)
        if not flagged:
            severity = "none"
        elif not multi:
            severity = flagged[0]["severity"]
        else:
            severity = "high" if low_share >= 0.3 else "medium"
        pricing[qid] = {
            "anomaly": bool(flagged),
            "severity": severity,
            "direction": "low" if low_share >= 0.3 else (biggest["direction"] if biggest else None),
            "signals": [f"{f['item'][:45]} is {' and '.join(f['signals'])}" for f in flagged] if multi else [sig for f in flagged for sig in f["signals"]],
            "lines": findings,
        }
    names = [state["quotes"][k]["supplier_name"] for k, v in pricing.items() if v["anomaly"]]
    return {
        "pricing": pricing,
        "market": markets[0] if markets else None,
        "trace": _step("pricing_auditor", f"Flagged unusual pricing: {', '.join(names)}." if names else "No unusual pricing found."),
    }


def recommender(state: State) -> dict:
    """Combine cost, risk and reliability into a ranked recommendation."""
    rfq = RFQ(**state["rfq"])
    entries = [
        {
            "id": qid,
            "supplier_name": state["intel"][qid]["supplier"]["name"] if state["intel"][qid]["matched"] else q["supplier_name"],
            "quote": ParsedQuote(**q),
            "intel": state["intel"][qid],
            "cost": state["costs"][qid],
            "risk": state["risks"][qid],
            "pricing": state["pricing"][qid],
            "lines": state["lines"][qid],
            "not_requested": state["extras"][qid],
        }
        for qid, q in state["quotes"].items()
    ]
    outcome = score_quotes(entries, rfq)
    split = best_split(entries, rfq, state["rfq_lines"], outcome["recommendation"]["id"])
    for e in entries:
        e["quote"] = e["quote"].model_dump()
    by_id = {e["id"]: e for e in entries}
    result = {
        "rfq": rfq.model_dump(),
        "category": state["category"],
        "lines": state["rfq_lines"],
        "recommendation": outcome["recommendation"],
        "split_award": split,
        "quotes": [by_id[qid] for qid in outcome["ranking"]],
        "rejected": state["rejected"],
        "market": state["market"],
    }
    rec = outcome["recommendation"]
    summary = f"Recommended {rec['supplier_name']} with score {rec['score']:.0f} ({rec['confidence']} confidence)."
    if split:
        summary += f" Splitting the award across {len(split['suppliers'])} suppliers would save ${split['saving_vs_recommended_usd']:,.0f}."
    return {"result": result, "trace": _step("recommender", summary)}


def narrator(state: State) -> dict:
    """Write the executive summary: Claude if configured, otherwise a template."""
    result = dict(state["result"])
    brief = {
        "rfq": result["rfq"],
        "recommendation": result["recommendation"],
        "split_award": result["split_award"],
        "quotes": [
            {
                "supplier": e["supplier_name"],
                "rank": e["rank"],
                "headline_usd": e["cost"]["headline_usd"],
                "landed_usd": e["cost"]["landed_usd"],
                "risk_adjusted_usd": e["risk_adjusted_usd"],
                "p_late": round(e["risk"]["p_late"], 3),
                "lead_days": e["risk"]["lead_days"],
                "scores": e["scores"],
                "cost_lines_outside_headline": [c for c in e["cost"]["components"] if c["status"] != "quoted"],
                "flags": e["flags"],
            }
            for e in result["quotes"]
        ],
    }
    text = llm.narrate(brief)
    result["narrative"] = text or template_narrative(result["recommendation"], result["quotes"], result["split_award"])
    result["narrative_source"] = "llm" if text else "template"
    return {"result": result, "trace": _step("narrator", f"Summary written ({result['narrative_source']}).")}


def _route_after_intake(state: State) -> str:
    return "supplier_intel" if state["quotes"] else END


@lru_cache(maxsize=1)
def build_graph():
    g = StateGraph(State)
    for fn in (intake, supplier_intel, cost_analyst, risk_analyst, pricing_auditor, recommender, narrator):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", _route_after_intake, ["supplier_intel", END])
    analysts = ["cost_analyst", "risk_analyst", "pricing_auditor"]
    for name in analysts:
        g.add_edge("supplier_intel", name)
    g.add_edge(analysts, "recommender")
    g.add_edge("recommender", "narrator")
    g.add_edge("narrator", END)
    return g.compile()


class NoUsableQuotes(ValueError):
    pass


def run_comparison(documents: list[dict], rfq: RFQ) -> dict:
    final = build_graph().invoke({"rfq": rfq.model_dump(), "documents": documents, "trace": []})
    if not final.get("quotes"):
        reasons = "; ".join(f"{r['file']}: {r['reason']}" for r in final.get("rejected", []))
        raise NoUsableQuotes(f"None of the uploaded documents could be read as a quotation. {reasons}")
    result = final["result"]
    result["trace"] = final["trace"]
    result["assumptions"] = {
        "buyer_country": settings.buyer_country,
        "base_currency": settings.base_currency,
        "fx_rates": settings.fx_rates,
        "cost_of_capital": settings.cost_of_capital,
        "import_duty_rate": settings.import_duty_rate,
        "delay_cost_rate_per_day": settings.delay_cost_rate_per_day,
        "moq_excess_cost_factor": settings.moq_excess_cost_factor,
    }
    result["risk_model"] = risk_model.model_meta()["metrics"]
    return result
