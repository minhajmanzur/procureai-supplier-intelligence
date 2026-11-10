"""Landed-cost model: what a quotation really costs once everything is counted.

Every cost line carries a status:
  quoted       - in the supplier's headline total
  hidden       - stated in the quote's terms but left out of the headline total
  estimated    - not priced by the supplier at all; the buyer bears it (Incoterm gap)
  unquantified - buyer bears it, but there is no basis to estimate; ask the supplier
"""
from __future__ import annotations

from app.analysis.fmt import price
from app.config import settings
from app.schemas import RFQ, ParsedQuote

# What the buyer still has to pay for under each Incoterm.
BUYER_BEARS = {
    "EXW": ("origin", "freight", "insurance", "duty"),
    "FCA": ("freight", "insurance", "duty"),
    "FAS": ("freight", "insurance", "duty"),
    "FOB": ("freight", "insurance", "duty"),
    "CFR": ("insurance", "duty"),
    "CPT": ("insurance", "duty"),
    "CIF": ("duty",),
    "CIP": ("duty",),
    "DAP": ("duty",),
    "DPU": ("duty",),
    "DDP": (),
}
LABELS = {
    "origin": "Origin handling and export clearance",
    "freight": "Main-carriage freight",
    "insurance": "Cargo insurance",
    "duty": "Import duty",
    "handling": "Handling surcharge",
    "packaging": "Packaging",
    "tooling": "Tooling / setup charge",
    "documentation": "Documentation fees",
    "surcharge": "Surcharge",
    "bank": "Bank charges",
}


def landed_cost(
    quote: ParsedQuote,
    rfq: RFQ,
    supplier: dict | None,
    lead_days: int,
    lines: list[dict] | None = None,
    extras_value: float = 0.0,
) -> dict:
    """lines: the RFQ lines as matched to this quote (see app.analysis.matching); each has
    item, requested_qty, quoted, and when quoted: description, quantity, unit_price in the
    quote currency; when not quoted: fill_usd, the per-unit price used to fill the gap.
    extras_value: value (quote currency) of quoted lines that were not requested."""
    s = settings
    warnings: list[str] = []
    fx = s.fx_rates.get(quote.currency)
    if fx is None:
        fx = 1.0
        warnings.append(f"No FX rate for {quote.currency}; amounts treated as {s.base_currency}.")

    if lines is None:  # no RFQ matching done: every goods line on the quote counts
        lines = [
            {"item": li.description, "requested_qty": rfq.quantity if len(quote.line_items) == 1 else li.quantity, "quoted": True,
             "description": li.description, "quantity": li.quantity, "unit_price": li.unit_price}
            for li in quote.line_items
        ]
    quoted = [ln for ln in lines if ln["quoted"]]
    quantity = sum(ln["quantity"] for ln in quoted)
    goods = sum(ln["quantity"] * ln["unit_price"] for ln in quoted) * fx
    unit_usd = goods / quantity if quantity else 0.0

    components: list[dict] = []
    for ln in quoted:
        if abs(ln["quantity"] - ln["requested_qty"]) > 0.5:
            warnings.append(f"Quoted quantity {ln['quantity']:,.0f} differs from the requested {ln['requested_qty']:,.0f} ({ln['item'][:40]}).")
        name = "Goods" if len(lines) == 1 else ln["description"][:60]
        components.append(
            {"kind": "goods", "label": f"{name} ({ln['quantity']:,.0f} x {price(ln['unit_price'] * fx)})",
             "amount": ln["quantity"] * ln["unit_price"] * fx, "status": "quoted", "evidence": ""}
        )
    # Items the supplier did not quote still have to be bought somewhere: price them at
    # this round's median so incomplete quotes can be compared with complete ones.
    gap = 0.0
    for ln in lines:
        if not ln["quoted"] and ln.get("fill_usd"):
            amount = ln["fill_usd"] * ln["requested_qty"]
            gap += amount
            components.append(
                {"kind": "gap", "label": f"Not quoted: {ln['item'][:50]} ({ln['requested_qty']:,.0f} x {price(ln['fill_usd'])}, this round's median)",
                 "amount": amount, "status": "estimated", "evidence": "This supplier did not quote the item; it must be sourced elsewhere."}
            )

    # 1) Charges the quote states explicitly
    stated = []
    for fee in quote.fees:
        if fee.basis == "fixed":
            # a per-piece charge follows the quantity bought; a flat charge does not
            share = quantity / fee.per_quantity if fee.per_quantity else 1.0
            stated.append((fee, fee.amount * fx * share))
        elif fee.basis == "percent_of_goods":
            stated.append((fee, goods * fee.percent / 100))
    stated_total = sum(a for _, a in stated)
    # items nobody asked for are taken out of the headline so quotes compare like for like
    headline = (quote.quoted_total - extras_value) * fx if quote.quoted_total else goods
    fees_in_headline = stated_total > 0 and abs(headline - (goods + stated_total)) <= 0.005 * headline
    if stated_total and not fees_in_headline and abs(headline - goods) > 0.005 * headline:
        warnings.append("Headline total matches neither goods alone nor goods plus stated charges; check the arithmetic.")
    for fee, amount in stated:
        label = LABELS.get(fee.kind, fee.kind.title())
        if fee.basis == "percent_of_goods":
            label += f" ({fee.percent:g}% of goods)"
        components.append(
            {"kind": fee.kind, "label": label, "amount": amount, "status": "quoted" if fees_in_headline else "hidden", "evidence": fee.evidence}
        )

    # 2) Costs the supplier leaves with the buyer (Incoterm gap)
    domestic = bool(supplier) and supplier["country"] == s.buyer_country
    distance = supplier["distance_km"] if supplier else 8000.0
    incoterm = quote.incoterm
    if incoterm is None:
        incoterm = "DAP" if domestic else "FCA"
        warnings.append(f"No Incoterm stated; assumed {incoterm} (buyer bears {', '.join(BUYER_BEARS[incoterm]) or 'nothing'}).")
    unpriced = {f.kind: f for f in quote.fees if f.basis == "unquantified"}
    priced_kinds = {f.kind for f, _ in stated}
    bears = list(BUYER_BEARS[incoterm]) + [k for k in ("freight", "insurance", "duty") if k in unpriced and k not in BUYER_BEARS[incoterm]]
    rates = {
        "origin": s.origin_charges_rate,
        "freight": s.freight_base_rate + s.freight_rate_per_1000km * distance / 1000,
        "insurance": s.insurance_rate,
        "duty": 0.0 if domestic else s.import_duty_rate,
    }
    for kind in bears:
        if kind in priced_kinds or rates[kind] == 0:
            continue
        evidence = unpriced[kind].evidence if kind in unpriced else f"{incoterm} terms: buyer bears {LABELS[kind].lower()}."
        components.append(
            {
                "kind": kind,
                "label": f"{LABELS[kind]} (est. {rates[kind]:.1%} of goods)",
                "amount": goods * rates[kind],
                "status": "estimated",
                "evidence": evidence,
            }
        )
    for kind, fee in unpriced.items():
        if kind not in rates:
            components.append(
                {"kind": kind, "label": f"{LABELS.get(kind, kind.title())} (amount not stated)", "amount": 0.0, "status": "unquantified", "evidence": fee.evidence}
            )

    # 3) Payment terms: cash paid early costs money, credit is worth money
    adv = quote.advance_payment_pct / 100
    financing = goods * s.cost_of_capital * (adv * lead_days - (1 - adv) * quote.net_payment_days) / 365
    if abs(financing) >= 1:
        terms = f"{quote.advance_payment_pct:g}% advance" if adv else ""
        if quote.net_payment_days:
            terms = f"{terms}, net {quote.net_payment_days}".lstrip(", ")
        components.append(
            {
                "kind": "financing",
                "label": f"Payment terms ({terms}) at {s.cost_of_capital:.0%} cost of capital",
                "amount": financing,
                "status": "hidden" if financing > 0 else "benefit",
                "evidence": "Cash paid before delivery is financed by the buyer; supplier credit offsets it.",
            }
        )

    # 4) Minimum order quantity above what was asked for
    # (a stated MOQ is only unambiguous when a single item is being bought)
    requested = lines[0]["requested_qty"] if len(lines) == 1 else None
    if quote.moq and requested and quote.moq > requested:
        excess_units = quote.moq - requested
        excess_value = excess_units * unit_usd
        components.append(
            {
                "kind": "moq",
                "label": f"MOQ {quote.moq} forces {excess_units} surplus units ({s.moq_excess_cost_factor:.0%} of their value counted)",
                "amount": excess_value * s.moq_excess_cost_factor,
                "status": "hidden",
                "evidence": f"Minimum order quantity {quote.moq} vs. {requested:,.0f} requested: {excess_value:,.0f} extra cash outlay.",
            }
        )

    for c in components:
        c["amount"] = round(c["amount"], 2)
    landed = sum(c["amount"] for c in components)
    return {
        "currency": quote.currency,
        "fx_rate": fx,
        "quantity": quantity,
        "coverage": {"quoted": len(quoted), "requested": len(lines)},
        "gap_usd": round(gap, 2),
        "unit_price_usd": round(unit_usd, 5),
        "goods_usd": round(goods, 2),
        "headline_usd": round(headline, 2),
        "landed_usd": round(landed, 2),
        "hidden_usd": round(landed - headline, 2),
        "hidden_pct_of_headline": round((landed - headline) / headline, 4) if headline else 0.0,
        "incoterm_used": incoterm,
        "components": components,
        "warnings": warnings,
    }
