"""Split-award analysis: would buying different items from different suppliers be cheaper?

Every way of assigning the requested items to the eligible suppliers is costed with the
same landed-cost model as a single award. A supplier's fixed charges (tooling,
documentation, packing) are paid in full whenever it wins at least one item, so a split
only wins when the per-item price advantage outweighs paying two sets of fixed charges.
"""
from __future__ import annotations

from itertools import combinations, product

from app.analysis.costing import landed_cost
from app.config import settings
from app.schemas import RFQ

MAX_LINES, MAX_SUPPLIERS = 6, 6
MIN_SAVING_SHARE, MIN_SAVING_USD = 0.01, 25.0


def best_split(entries: list[dict], rfq: RFQ, rfq_lines: list[dict], recommended_id: str | None = None) -> dict | None:
    """entries: scored quotes (after score_quotes). Returns the cheapest multi-supplier
    award if it beats the best single-supplier award by a meaningful margin, else None."""
    n = len(rfq_lines)
    eligible = [e for e in entries if not e["held"]]
    if n < 2 or n > MAX_LINES or not 2 <= len(eligible) <= MAX_SUPPLIERS:
        return None

    # cost of awarding supplier e exactly the item set S, for every S it can supply
    cost: dict[tuple[str, frozenset], dict] = {}
    for e in eligible:
        can = [ln["rfq_index"] for ln in e["lines"] if ln["quoted"]]
        delay_days = e["intel"]["profile"].get("avg_days_late_when_late") or 7.0
        for size in range(1, len(can) + 1):
            for subset in combinations(can, size):
                rows = [ln for ln in e["lines"] if ln["rfq_index"] in subset]
                c = landed_cost(e["quote"], rfq, e["intel"]["supplier"], e["risk"]["lead_days"], lines=rows)
                delay = e["risk"]["p_late"] * delay_days * settings.delay_cost_rate_per_day * c["goods_usd"]
                cost[(e["id"], frozenset(subset))] = {"total": c["landed_usd"] + delay, "goods": c["goods_usd"]}

    offers = [[e["id"] for e in eligible if ln_quoted(e, i)] for i in range(n)]
    if any(not o for o in offers):
        return None  # some item has no eligible quote at all

    best_total, best_assignment = None, None
    for assignment in product(*offers):
        if len(set(assignment)) < 2:
            continue
        total = sum(
            cost[(sid, frozenset(i for i, a in enumerate(assignment) if a == sid))]["total"] for sid in set(assignment)
        )
        if best_total is None or total < best_total:
            best_total, best_assignment = total, assignment
    if best_assignment is None:
        return None

    single = min(eligible, key=lambda e: e["risk_adjusted_usd"])
    saving = single["risk_adjusted_usd"] - best_total
    if saving < max(MIN_SAVING_USD, MIN_SAVING_SHARE * single["risk_adjusted_usd"]):
        return None

    by_id = {e["id"]: e for e in eligible}
    recommended = by_id.get(recommended_id, single)
    suppliers = []
    for sid in dict.fromkeys(best_assignment):
        items = frozenset(i for i, a in enumerate(best_assignment) if a == sid)
        suppliers.append(
            {
                "id": sid,
                "supplier_name": by_id[sid]["supplier_name"],
                "items": [rfq_lines[i]["item"] for i in sorted(items)],
                "cost_usd": round(cost[(sid, items)]["total"], 2),
                "p_late": by_id[sid]["risk"]["p_late"],
                "lead_days": by_id[sid]["risk"]["lead_days"],
            }
        )
    return {
        "suppliers": suppliers,
        "assignment": [{"item": rfq_lines[i]["item"], "supplier_name": by_id[sid]["supplier_name"]} for i, sid in enumerate(best_assignment)],
        "total_usd": round(best_total, 2),
        "single_award_supplier": single["supplier_name"],
        "single_award_usd": single["risk_adjusted_usd"],
        "saving_usd": round(saving, 2),
        "saving_pct": round(saving / single["risk_adjusted_usd"], 4),
        "recommended_award_usd": recommended["risk_adjusted_usd"],
        "saving_vs_recommended_usd": round(recommended["risk_adjusted_usd"] - best_total, 2),
        "slowest_lead_days": max(s["lead_days"] for s in suppliers),
    }


def ln_quoted(entry: dict, rfq_index: int) -> bool:
    return any(ln["rfq_index"] == rfq_index and ln["quoted"] for ln in entry["lines"])
