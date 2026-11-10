"""Render a comparison run as a PDF report that can be filed with the purchase order.

build_report(result) takes the dict returned by run_comparison (or stored in
comparison_runs.result) and returns PDF bytes. Page 1 is the decision; the pages after
it are the evidence: cost breakdowns with the sentence each charge came from, flags,
risk drivers, and the assumptions and model metrics behind the numbers.
"""
from __future__ import annotations

import io
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

WIDTH = A4[0] - 32 * mm
INK, INK2, MUTED, RULE = colors.HexColor("#16202c"), colors.HexColor("#52514e"), colors.HexColor("#7a7f87"), colors.HexColor("#d9dee5")
GOOD, ACCENT, SOFT = colors.HexColor("#14804a"), colors.HexColor("#0f5fd7"), colors.HexColor("#f3f5f8")
# cost-bar segments, in fixed order (validated categorical slots; identity is also carried by the legend)
SEGMENTS = [
    ("quoted", "In the quoted total", colors.HexColor("#2a78d6")),
    ("hidden", "Stated but left out of the total", colors.HexColor("#eb6834")),
    ("estimated", "Left to the buyer (estimated)", colors.HexColor("#1baf7a")),
    ("delay", "Expected cost of delay", colors.HexColor("#eda100")),
]

_base = getSampleStyleSheet()["Normal"]
BODY = ParagraphStyle("body", parent=_base, fontName="Helvetica", fontSize=9.2, leading=12.6, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7.8, leading=10, textColor=MUTED)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.6, leading=11)
CELL_R = ParagraphStyle("cell_r", parent=CELL, alignment=TA_RIGHT)
HEAD_R = ParagraphStyle("head_r", parent=CELL_R, fontName="Helvetica-Bold", fontSize=7.4, leading=9.4, textColor=INK2)
H1 = ParagraphStyle("h1", parent=BODY, fontName="Helvetica-Bold", fontSize=18, leading=22)
H2 = ParagraphStyle("h2", parent=BODY, fontName="Helvetica-Bold", fontSize=12, leading=15, spaceBefore=12, spaceAfter=5)
H3 = ParagraphStyle("h3", parent=BODY, fontName="Helvetica-Bold", fontSize=9.6, leading=12, spaceBefore=7, spaceAfter=2)
KICKER = ParagraphStyle("kicker", parent=SMALL, fontName="Helvetica-Bold", fontSize=7.6, textColor=GOOD)
BIG = ParagraphStyle("big", parent=BODY, fontName="Helvetica-Bold", fontSize=15, leading=19)
BULLET = ParagraphStyle("bullet", parent=BODY, leftIndent=9, bulletIndent=0, spaceAfter=1.5)


def _t(value) -> str:
    """Text safe for a Paragraph: XML-escaped, and limited to what the built-in font can draw."""
    return escape(str(value if value is not None else "").encode("latin-1", "replace").decode("latin-1"))


def _usd(x: float) -> str:
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


def _unit(x: float) -> str:
    return f"${x:,.4f}" if abs(x) < 1 else f"${x:,.2f}"


def _value(v) -> str:
    """A feature value in brackets, without float noise: 11.0 -> (11), 200000.0 -> (200,000)."""
    if v is None:
        return ""
    return f" ({v:,.0f})" if float(v).is_integer() else f" ({v:g})"


def _pct(x) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _p(text: str, style=BODY) -> Paragraph:
    return Paragraph(text, style)


def _table(rows, widths, header=True, right_from=1, zebra=False) -> Table:
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 8.6), ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (right_from, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
    ]
    if header:
        style += [("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("TEXTCOLOR", (0, 0), (-1, 0), INK2), ("FONTSIZE", (0, 0), (-1, 0), 7.8),
                  ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK2)]
    if zebra:
        style += [("BACKGROUND", (0, i), (-1, i), SOFT) for i in range(2, len(rows), 2)]
    t.setStyle(TableStyle(style))
    return t


def _cost_chart(quotes: list[dict]) -> Drawing:
    """One horizontal stacked bar per supplier: what the headline shows vs. what it really costs."""
    label_w, value_w, bar_h, gap = 185, 52, 11, 8
    plot_w = WIDTH - label_w - value_w
    h = len(quotes) * (bar_h + gap) + 26
    d = Drawing(WIDTH, h)
    top = h - 4
    biggest = max(q["risk_adjusted_usd"] for q in quotes) or 1.0
    for i, q in enumerate(quotes):
        y = top - (i + 1) * (bar_h + gap) + gap
        parts = {k: sum(c["amount"] for c in q["cost"]["components"] if c["status"] == k and c["amount"] > 0) for k in ("quoted", "hidden", "estimated")}
        parts["delay"] = q["delay_cost_usd"]
        name = q["supplier_name"] + (" (held)" if q["held"] else "")
        if len(name) > 42:
            name = name[:41].rstrip() + "..."
        d.add(String(0, y + 2.5, name.encode("latin-1", "replace").decode("latin-1"), fontName="Helvetica", fontSize=8.2, fillColor=INK))
        x = label_w
        for key, _, colour in SEGMENTS:
            w = parts[key] / biggest * plot_w
            if w > 0.3:
                d.add(Rect(x, y, max(w - 1.2, 0.3), bar_h, fillColor=colour, strokeColor=None))  # 1.2pt white gap between segments
                x += w
        d.add(String(WIDTH, y + 2.5, _usd(q["risk_adjusted_usd"]), fontName="Helvetica-Bold", fontSize=8.2, fillColor=INK, textAnchor="end"))
    d.add(Line(label_w, 20, label_w, top - gap + 2, strokeColor=RULE, strokeWidth=0.6))
    x = 0
    for _, label, colour in SEGMENTS:
        d.add(Rect(x, 3, 8, 8, fillColor=colour, strokeColor=None))
        d.add(String(x + 11, 4, label, fontName="Helvetica", fontSize=7.6, fillColor=INK2))
        x += 11 + len(label) * 3.9 + 12
    return d


def _bullets(items: list[str]) -> list:
    return [Paragraph(text, BULLET, bulletText="•") for text in items]


def _footer(run_id, generated: str):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        left = f"ProcureAI comparison report{f' - run #{run_id}' if run_id else ''} - generated {generated}"
        canvas.drawString(16 * mm, 9 * mm, left)
        canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def build_report(result: dict, generated_at: datetime | None = None) -> bytes:
    quotes = result["quotes"]
    rec = result["recommendation"]
    rfq = result["rfq"]
    lines = result.get("lines") or [{"item": rfq["item"], "quantity": rfq["quantity"]}]
    multi = len(lines) > 1
    split = result.get("split_award")
    run_id = result.get("run_id")
    generated = (generated_at or datetime.now()).strftime("%d %b %Y %H:%M")
    story: list = []

    # ---- page 1: the decision -------------------------------------------------
    story += [_p("Quotation comparison report", H1), _p(f"{len(quotes)} quotations compared - {generated}", SMALL), Spacer(1, 8)]
    rfq_rows = [["Requested item", "Quantity"]] + [[_p(_t(ln["item"]), CELL), f"{ln['quantity']:,}"] for ln in lines]
    story.append(_table(rfq_rows, [WIDTH - 30 * mm, 30 * mm]))
    if rfq.get("needed_in_days"):
        story.append(_p(f"Needed within {rfq['needed_in_days']} days.", SMALL))
    story.append(Spacer(1, 10))

    confidence = rec.get("confidence") or "n/a"
    box = [
        [_p("RECOMMENDED AWARD", KICKER)],
        [_p(f"{_t(rec['supplier_name'])} <font size=9 color='#52514e'>&nbsp; score {rec['score']:.0f}/100, {confidence} confidence</font>", BIG)],
    ]
    for para in result.get("narrative", "").split("\n\n")[1:]:  # the first paragraph repeats the headline above
        if not (split and para.startswith("Split-award option")):  # shown as its own table below
            box.append([_p(_t(para))])
    panel = Table(box, colWidths=[WIDTH])
    panel.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SOFT), ("LINEBEFORE", (0, 0), (0, -1), 3, GOOD),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, 0), 8), ("BOTTOMPADDING", (0, -1), (-1, -1), 9),
    ]))
    story.append(panel)

    if rec.get("before_signing"):
        story += [_p("Before signing", H3)] + _bullets([_t(x) for x in rec["before_signing"]])

    if split:
        rows = [["Split-award option", "Items", "Cost", "P(late)", "Lead"]]
        for sp in split["suppliers"]:
            rows.append([_p(_t(sp["supplier_name"]), CELL), _p("<br/>".join(_t(i) for i in sp["items"]), CELL), _usd(sp["cost_usd"]), _pct(sp["p_late"]), f"{sp['lead_days']} d"])
        rows.append([_p("<b>Split total</b>", CELL), "", _usd(split["total_usd"]), "", f"{split['slowest_lead_days']} d"])
        story += [
            _p(f"Split-award option: save {_usd(split['saving_vs_recommended_usd'])} by using {len(split['suppliers'])} suppliers", H2),
            _table(rows, [52 * mm, WIDTH - 52 * mm - 54 * mm, 22 * mm, 16 * mm, 16 * mm], right_from=2),
            _p(f"Risk-adjusted, each supplier's fixed charges counted in full. Compared with {_usd(split['recommended_award_usd'])} for the recommended "
               f"single award and {_usd(split['single_award_usd'])} for the cheapest single award ({_t(split['single_award_supplier'])}).", SMALL),
        ]

    story.append(KeepTogether([_p("Headline price vs. what it really costs", H2), _cost_chart(quotes)]))

    head = ["#", "Supplier"] + (["Items"] if multi else []) + ["Headline", "Landed", "Risk-adj.", "P(late)", "Lead", "On-time", "Score"]
    rows = [head]
    for q in quotes:
        on_time = _pct(q["intel"]["profile"].get("on_time_rate")) if q["intel"]["matched"] else "n/a"
        cov = q["cost"].get("coverage", {"quoted": 1, "requested": 1})
        rows.append(
            ["-" if q["held"] else str(q["rank"]), _p(_t(q["supplier_name"]) + (" <font color='#b3261e'><b>held</b></font>" if q["held"] else ""), CELL)]
            + ([f"{cov['quoted']} of {cov['requested']}"] if multi else [])
            + [_usd(q["cost"]["headline_usd"]), _usd(q["cost"]["landed_usd"]), _usd(q["risk_adjusted_usd"]), _pct(q["risk"]["p_late"]),
               f"{q['risk']['lead_days']} d", on_time, f"{q['scores']['total']:.1f}"]
        )
    fixed = [8 * mm] + ([14 * mm] if multi else []) + [20 * mm, 20 * mm, 20 * mm, 15 * mm, 13 * mm, 16 * mm, 13 * mm]
    widths = [fixed[0], WIDTH - sum(fixed)] + fixed[1:]
    story.append(KeepTogether([
        _p("Comparison", H2),
        _table(rows, widths, right_from=2),
        _p("Landed = goods plus every charge and buyer-borne cost. Risk-adjusted = landed plus the expected cost of late delivery. "
           "P(late) is the model's predicted probability of late delivery; On-time is the supplier's historical rate.", SMALL),
    ]))
    for r in result.get("rejected", []):
        story.append(_p(f"Could not read {_t(r['file'])}: {_t(r['reason'])}", SMALL))

    if multi:
        short = [" ".join(q["supplier_name"].split()[:2]) for q in quotes]
        rows = [["Item", "Qty"] + [_p(_t(s), HEAD_R) for s in short]]
        for i, ln in enumerate(lines):
            offers = [q["lines"][i] for q in quotes]
            low = min((o["unit_price_usd"] for o in offers if o["quoted"]), default=None)
            cells = [(_p(f"<b>{_unit(o['unit_price_usd'])}</b>" if o["unit_price_usd"] == low else _unit(o["unit_price_usd"]), CELL_R) if o["quoted"]
                      else _p("<font color='#7a7f87'>not quoted</font>", CELL_R)) for o in offers]
            rows.append([_p(_t(ln["item"]), CELL), f"{ln['quantity']:,}"] + cells)
        item_w = 46 * mm if len(quotes) > 4 else 58 * mm
        col = (WIDTH - item_w - 18 * mm) / len(quotes)
        story += [_p("Unit price by item", H2), _table(rows, [item_w, 18 * mm] + [col] * len(quotes)),
                  _p("USD per piece, before freight, duty and other charges. The lowest quoted price for each item is in bold.", SMALL)]

    # ---- evidence: one section per quotation ------------------------------------
    story.append(PageBreak())
    story.append(_p("Quote by quote", H1))
    for q in quotes:
        quote, cost, risk = q["quote"], q["cost"], q["risk"]
        title = f"{'Held' if q['held'] else q['rank']}. {_t(q['supplier_name'])}"
        meta = [quote.get("source_file", ""), quote["currency"], cost["incoterm_used"], f"lead {risk['lead_days']} days"]
        if "ocr" in quote.get("parse_method", ""):
            meta.insert(1, "read by OCR")
        rows = [["Landed cost", "Status", "USD"]]
        for c in cost["components"]:
            label = _t(c["label"]) + (f"<br/><font size=7.4 color='#7a7f87'>{_t(c['evidence'])}</font>" if c.get("evidence") else "")
            rows.append([_p(label, CELL), "" if c["status"] == "quoted" else c["status"], _usd(c["amount"])])
        rows.append([_p(f"Expected cost of delay ({_pct(risk['p_late'])} x {q['expected_delay_days']} days expected)", CELL), "", _usd(q["delay_cost_usd"])])
        rows.append([_p("<b>Risk-adjusted total</b>", CELL), "", _usd(q["risk_adjusted_usd"])])
        s = q["scores"]
        block = [
            _p(title, H2),
            _p(_t(" - ".join(str(m) for m in meta if m)), SMALL),
            Spacer(1, 3),
            _table(rows, [WIDTH - 46 * mm, 24 * mm, 22 * mm]),
            _p(f"Scores: cost {s['cost']:.0f}, delivery {s['delivery_risk']:.0f}, reliability {s['reliability']:.0f}, lead time {s['lead_time']:.0f}; "
               f"total {s['total']:.1f}. Headline {_usd(cost['headline_usd'])} (rank {q['headline_rank']} by headline).", SMALL),
        ]
        story.append(KeepTogether(block[:4]))
        story.append(block[4])
        if q["flags"]:
            story += [_p("Flags", H3)] + _bullets([f"<b>{f['severity'].upper()}</b> &nbsp;{_t(f['text'])}" for f in q["flags"]])
        if risk.get("drivers"):
            story.append(_p("What drives the delivery-risk prediction", H3))
            story += _bullets([_t(f"{d['label']}{_value(d['value'])}: {d['effect']}") for d in risk["drivers"]])
        if q.get("negotiation_levers"):
            story += [_p("Negotiation levers", H3)] + _bullets([_t(x) for x in q["negotiation_levers"]])
        story.append(Spacer(1, 6))

    # ---- method ------------------------------------------------------------------
    a, m = result.get("assumptions", {}), result.get("risk_model", {})
    story.append(_p("Assumptions and method", H2))
    facts = []
    if a:
        fx = ", ".join(f"{k} {v:g}" for k, v in a.get("fx_rates", {}).items() if k != a.get("base_currency"))
        facts += [
            f"Buyer located in {_t(a.get('buyer_country'))}; all amounts in {_t(a.get('base_currency'))}. FX rates used: {_t(fx)}.",
            f"Cost of capital {a.get('cost_of_capital', 0):.0%} a year (payment terms); import duty {a.get('import_duty_rate', 0):.0%} of goods where the buyer bears it; "
            f"cost of delay {a.get('delay_cost_rate_per_day', 0):.1%} of goods value per day late.",
            "Freight, insurance and duty marked 'estimated' are not supplier figures: they are this report's estimates for costs the Incoterm leaves with the buyer.",
        ]
    if m:
        facts.append(f"Delivery risk: gradient-boosted model (XGBoost) trained on {m.get('train_rows', 0):,} past orders, ROC AUC {m.get('roc_auc', 0):.2f} on the "
                     f"newest {m.get('test_rows', 0):,} orders held out. It is a probability estimate, not a guarantee.")
    facts.append("A quotation whose price is abnormally low on several independent checks is held for verification and cannot be the recommended award.")
    story += _bullets(facts)
    if result.get("trace"):
        story.append(_p("Agent trace", H3))
        story += [_p(f"<b>{_t(t['agent'])}</b> &nbsp;{_t(t['summary'])}", SMALL) for t in result["trace"]]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=15 * mm, bottomMargin=16 * mm,
                            title="Quotation comparison report", author="ProcureAI")
    footer = _footer(run_id, generated)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
