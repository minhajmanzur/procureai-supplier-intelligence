"""Command-line comparison.

    python -m app.cli                      # the five bundled servo-motor quotations
    python -m app.cli a.pdf b.txt --qty 200 --item "Planetary gearbox"
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app.agents.graph import run_comparison
from app.bootstrap import bootstrap
from app.config import ROOT
from app.ingestion.parser import load_document
from app.schemas import RFQ, RFQLine


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare supplier quotations.")
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--item", default="Industrial servo motor 400W")
    ap.add_argument("--qty", type=int, default=500)
    ap.add_argument("--needed-in-days", type=int, default=30)
    ap.add_argument("--report", type=Path, metavar="FILE.pdf", help="also write the comparison as a PDF report")
    ap.add_argument("--line", action="append", default=[], metavar="QTY:ITEM", help="several items: repeat, e.g. --line \"50000:Hex bolt M10x50\"")
    args = ap.parse_args()

    bootstrap()
    files = args.files or sorted((ROOT / "samples" / "quotes").glob("*.txt"))
    docs = [load_document(p.name, p.read_bytes()) for p in files]
    lines = [RFQLine(quantity=int(qty.replace(",", "")), item=item.strip()) for qty, item in (row.split(":", 1) for row in args.line)]
    rfq = RFQ(item=args.item, quantity=args.qty, needed_in_days=args.needed_in_days, lines=lines)
    result = run_comparison(docs, rfq)

    print(f"\nRFQ, needed in {args.needed_in_days} days:")
    for ln in rfq.all_lines():
        print(f"  {ln.quantity:>9,} x {ln.item}")
    print()
    print(f"{'#':<2} {'Supplier':<34}{'Headline':>10}{'Landed':>10}{'Risk-adj':>10}{'P(late)':>9}{'Lead':>6}{'Score':>7}")
    for e in result["quotes"]:
        print(
            f"{'-' if e['held'] else e['rank']:<2} {(e['supplier_name'] + (' (held)' if e['held'] else ''))[:33]:<34}{e['cost']['headline_usd']:>10,.0f}{e['cost']['landed_usd']:>10,.0f}"
            f"{e['risk_adjusted_usd']:>10,.0f}{e['risk']['p_late']:>9.0%}{e['risk']['lead_days']:>5}d{e['scores']['total']:>7.1f}"
        )
    print("\nCosts outside the headline total:")
    for e in result["quotes"]:
        extra = [c for c in e["cost"]["components"] if c["status"] != "quoted"]
        print(f"  {e['supplier_name']}: {e['cost']['hidden_usd']:+,.0f}")
        for c in extra:
            print(f"      {c['amount']:>9,.0f}  [{c['status']}] {c['label']}")
    print("\nFlags:")
    for e in result["quotes"]:
        for f in e["flags"]:
            print(f"  [{f['severity']:<6}] {e['supplier_name']}: {f['text']}")
    if len(result["lines"]) > 1:
        print("\nUnit price by item (USD):")
        for i, ln in enumerate(result["lines"]):
            cells = [f"{e['supplier_name'].split()[0]} {e['lines'][i]['unit_price_usd']:.4f}" if e["lines"][i]["quoted"] else f"{e['supplier_name'].split()[0]} n/q" for e in result["quotes"]]
            print(f"  {ln['item'][:38]:<40}" + " | ".join(cells))
    print("\n" + result["narrative"])
    if args.report:
        from app.report import build_report

        args.report.write_bytes(build_report(result))
        print(f"\nReport written to {args.report}")
    print("\nAgent trace:")
    for t in result["trace"]:
        print(f"  {t['agent']:<16} {t['summary']}")


if __name__ == "__main__":
    main()
