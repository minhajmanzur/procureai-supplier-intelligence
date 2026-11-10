"""Print what the parser extracts from each quotation file.

Run:  python scripts/show_parse.py samples/fasteners_pdf
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ingestion.parser import IMAGE_TYPES, load_document, parse_quote  # noqa: E402

folder = Path(sys.argv[1] if len(sys.argv) > 1 else "samples/quotes")
for path in sorted(p for p in folder.iterdir() if p.suffix.lower() in (".pdf", ".txt", *IMAGE_TYPES)):
    doc = load_document(path.name, path.read_bytes())
    if "--text" in sys.argv:
        print(doc["text"], "\n" + "-" * 60)
    q = parse_quote(doc["text"], path.name)
    if doc["ocr"]:
        print(f"[OCR {doc['ocr']}]")
    items = [(li.quantity, round(li.unit_price, 5)) for li in q.line_items]
    print(f"{path.name}\n  {q.supplier_name} | {q.currency} {items} total {q.quoted_total}")
    print(f"  lead {q.lead_time_days}d | {q.incoterm} | advance {q.advance_payment_pct:g}% net {q.net_payment_days} | "
          f"MOQ {q.moq} | valid {q.validity_days}d | escalation {bool(q.price_escalation_clause)}")
    for f in q.fees:
        print(f"    fee {f.kind:<14}{f.basis:<18}{f.amount or f.percent or '':<10} <- {f.evidence[:80]}")
    for w in q.parse_warnings:
        print(f"    warning: {w}")
