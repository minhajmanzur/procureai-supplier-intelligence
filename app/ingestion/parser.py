"""Turn a supplier quotation (free text or PDF) into a structured ParsedQuote.

Rule-based extraction runs first: it is fast, free, deterministic and testable. It
handles key/value layouts, pipe or column tables and prose emails. If it cannot find
the essentials and an LLM is configured, app.agents.llm takes over as a fallback.
"""
from __future__ import annotations

import io
import re
from collections import Counter

from app.schemas import Fee, LineItem, ParsedQuote

NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
CUR = r"USD|EUR|GBP|INR|CNY|TRY|US\$|\$|€|£"
CUR_CODES = {"US$": "USD", "$": "USD", "€": "EUR", "£": "GBP"}

MONEY_RE = re.compile(rf"(?:({CUR})\s*({NUM}))|(?:({NUM})\s*(USD|EUR|GBP|INR|CNY|TRY)\b)")
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
INCOTERM_RE = re.compile(r"\b(EXW|FCA|FAS|FOB|CFR|CIF|CPT|CIP|DAP|DPU|DDP)\b")

FEE_KINDS: dict[str, str] = {
    "freight": r"freight|shipping|transport(?:ation)?|carriage",
    "insurance": r"insurance",
    "duty": r"customs|dut(?:y|ies)|tariffs?|\bVAT\b|\bGST\b",
    "handling": r"handling",
    "packaging": r"packaging|packing|crat(?:es|ing)|pallets?",
    "tooling": r"tooling|set-?up (?:charge|fee|cost)|mou?ld|\bNRE\b",
    "documentation": r"documentation|certificate of origin|inspection fee|certificates?\b|\bPPAP\b",
    "surcharge": r"surcharge|levy",
    "bank": r"bank (?:charges?|fees?)|wire fee|letter of credit",
}
FEE_RES = {k: re.compile(v, re.I) for k, v in FEE_KINDS.items()}

EXCLUDED_RE = re.compile(
    r"not included|not covered|excluded|excluding|exclusive of|\bextra\b|\badditional\b"
    r"|buyer'?s?(?:'s)? (?:account|cost|expense)|\b\w+ by (?:the )?buyer\b",  # borne / paid / arranged / covered by the buyer
    re.I,
)
INCLUDED_RE = re.compile(r"\bincluded\b|\binclusive\b|\bincludes\b|free of charge|no charge|waived|prepaid", re.I)
NO_EXTRA_RE = re.compile(r"\b(?:no|without|at no) (?:additional|extra)\b", re.I)
ESCALATION_RE = re.compile(r"subject to (?:change|adjustment|revision|escalation)|price escalation|may be (?:revised|adjusted)", re.I)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _money(text: str) -> list[tuple[float, str]]:
    out = []
    for m in MONEY_RE.finditer(text):
        cur, amt = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        out.append((_num(amt), CUR_CODES.get(cur, cur)))
    return out


def extract_text(filename: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages:
            # Layout mode keeps each table row on one line with its column gaps; plain
            # mode emits one cell per line. Fall back to plain if layout mode fails.
            try:
                pages.append(page.extract_text(extraction_mode="layout") or "")
            except Exception:
                pages.append(page.extract_text() or "")
        return "\n".join(pages)
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")
MIN_TEXT_CHARS = 40  # a PDF page set with less text than this is treated as a scan


def load_document(filename: str, data: bytes) -> dict:
    """Read an uploaded file into {"filename", "text", "ocr"}.

    Text files and PDFs with a text layer are read directly. Photos and scanned PDFs
    go through OCR; `ocr` then holds its statistics (pages, mean confidence), or an
    `error` if OCR is not installed.
    """
    name = filename.lower()
    doc = {"filename": filename, "text": "", "ocr": None}
    is_image = name.endswith(IMAGE_TYPES)
    if not is_image:
        doc["text"] = extract_text(filename, data)
    if is_image or (name.endswith(".pdf") and len(doc["text"].strip()) < MIN_TEXT_CHARS):
        from app.ingestion import ocr

        try:
            doc["text"], doc["ocr"] = ocr.ocr_document(filename, data)
        except ocr.OcrUnavailable as exc:
            doc["ocr"] = {"error": str(exc)}
    return doc


# --- field extractors ------------------------------------------------------


def _supplier(lines: list[str]) -> str | None:
    for line in lines:
        m = re.match(r"\s*(?:supplier|vendor|from|company|seller)\s*[:\-]\s*(.+)", line, re.I)
        if m:
            return m.group(1).strip()
    for line in lines:
        if line.strip() and not re.match(r"\s*(quotation|quote|proforma|offer)\b", line, re.I):
            return line.strip().title() if line.isupper() else line.strip()
    return None


def _currency(text: str) -> str:
    m = re.search(r"currency\s*[:\-]\s*([A-Z]{3})\b", text, re.I)
    if m:
        return m.group(1).upper()
    found = Counter(cur for _, cur in _money(text))
    found.update(re.findall(r"\((USD|EUR|GBP|INR|CNY|TRY)\)", text))
    return found.most_common(1)[0][0] if found else "USD"


def _cells(line: str) -> list[str]:
    parts = line.split("|") if "|" in line else re.split(r"\s{2,}|\t", line.strip())
    return [p.strip() for p in parts if p.strip()]


PER_N = r"(1,?000|100|thousand|hundred|[MC])\b"  # price bases common for fasteners and small parts


def _basis(token: str) -> int:
    t = token.lower().replace(",", "")
    return 1000 if t in ("1000", "thousand", "m") else 100


def _per_piece(qty: float, price: float, total: float) -> float | None:
    """Price per piece given a row's qty, price and line total. Small parts are often
    priced per 100 or per 1000, so try those bases when qty x price misses the total."""
    if total <= 0:
        return None
    for basis in (1, 1000, 100):
        if abs(qty * price / basis - total) <= 0.01 * total:
            return price / basis
    return None


def _table_rows(lines: list[str]) -> tuple[list[LineItem], set[int]]:
    """Rows shaped like: description, qty, unit price, line total (qty x unit = total)."""
    items, used = [], set()
    cell_num = re.compile(rf"(?:{CUR})?\s*({NUM})(?:\s*(?:pcs|pieces|nos|units|ea)\.?)?$", re.I)
    for i, line in enumerate(lines):
        cells = _cells(line)
        if len(cells) < 4:
            continue
        nums = [(j, _num(m.group(1))) for j, c in enumerate(cells) if (m := cell_num.match(c))]
        if len(nums) < 3:
            continue
        (_, qty), (_, unit), (_, total) = nums[-3:]
        unit = _per_piece(qty, unit, total)
        if unit is None:
            continue
        numeric_idx = {j for j, _ in nums}
        desc = next((c for j, c in enumerate(cells) if j not in numeric_idx and re.search(r"[A-Za-z]{3}", c)), "item")
        items.append(LineItem(description=desc, quantity=qty, unit_price=unit))
        used.add(i)
    return items, used


def _vertical_rows(lines: list[str]) -> tuple[list[LineItem], set[int]]:
    """Some PDF extractors emit one table cell per line: description, qty, unit, total."""
    items, used = [], set()
    cell_num = re.compile(rf"\s*(?:{CUR})?\s*({NUM})\s*$")
    values = [_num(m.group(1)) if (m := cell_num.match(line)) else None for line in lines]
    i = 1
    while i + 2 < len(lines):
        qty, unit, total = values[i : i + 3]
        desc = lines[i - 1].strip()
        if None not in (qty, unit, total):
            unit = _per_piece(qty, unit, total)
        if None not in (qty, unit, total) and re.search(r"[A-Za-z]{3}", desc):
            items.append(LineItem(description=desc, quantity=qty, unit_price=unit))
            used.update(range(i - 1, i + 3))
            i += 3
        else:
            i += 1
    return items, used


def _single_item(text: str) -> LineItem | None:
    """Key/value ('Unit Price: USD 38.90') or prose ('500 units ... at $47.50 per unit')."""
    qty = re.search(r"(?<!order )(?:\bqty|\bquantity)\s*[:\-]\s*([\d,]+)", text, re.I) or re.search(
        r"\b([\d,]+)\s*(?:units|pcs|pieces|nos|sets)\b", text, re.I
    )
    unit = re.search(rf"(?:unit\s*price|price\s*per\s*unit|\brate)\s*[:\-]\s*(?:{CUR})?\s*({NUM})", text, re.I) or re.search(
        rf"(?:{CUR})\s*({NUM})\s*(?:per|/|a)\s*(?:unit|pc|piece|each)\b|(?:{CUR})\s*({NUM})\s+each\b", text, re.I
    )
    desc = re.search(r"(?:item|description|product)\s*[:\-]\s*(.+)", text, re.I)
    description = desc.group(1).strip() if desc else "item"
    # "Price per 1000 pcs: USD 89.00" or "USD 89.00 per 1000 pcs"
    per_n = re.search(rf"(?:price|rate)\s*(?:per|/)\s*{PER_N}\s*(?:pcs|pieces|nos)?\.?\s*[:\-]?\s*(?:{CUR})?\s*({NUM})", text, re.I)
    if per_n is None and (m := re.search(rf"(?:{CUR})\s*({NUM})\s*(?:per|/)\s*{PER_N}", text, re.I)):
        per_n_price, per_n_basis = m.group(1), m.group(2)
    elif per_n:
        per_n_price, per_n_basis = per_n.group(2), per_n.group(1)
    else:
        per_n_price = None
    if qty and per_n_price:
        return LineItem(description=description, quantity=_num(qty.group(1)), unit_price=_num(per_n_price) / _basis(per_n_basis))
    if not (qty and unit):
        return None
    price = next(g for g in unit.groups() if g)
    return LineItem(description=description, quantity=_num(qty.group(1)), unit_price=_num(price))


def _quoted_total(text: str) -> float | None:
    best: tuple[int, float] | None = None
    rank = {"grand": 3, "total": 2, "sub": 1}
    pattern = rf"\b(grand\s+total|total(?:\s+(?:amount|price|value))?|sub\s*-?total)(?:\s+of)?\s*[:\-]?\s*(?:{CUR})?\s*({NUM})"
    for m in re.finditer(pattern, text, re.I):
        label = m.group(1).lower()
        r = rank["grand"] if label.startswith("grand") else rank["sub"] if label.startswith("sub") else rank["total"]
        if best is None or r >= best[0]:
            best = (r, _num(m.group(2)))
    return best[1] if best else None


def _lead_time(text: str) -> int | None:
    m = re.search(
        r"(?:lead\s*time|deliver(?:y(?:\s+time)?|ed)?|dispatch|ship(?:ment|s|ped)?)\b[^.\n]{0,40}?"
        r"\b(\d+)(?:\s*(?:-|to)\s*(\d+))?\s*(?:business |working |calendar )?(day|week)s?\b",
        text,
        re.I,
    )
    if not m:
        return None
    n = int(m.group(2) or m.group(1))  # use the upper end of a range
    return n * 7 if m.group(3).lower() == "week" else n


def _payment(text: str) -> tuple[float, int]:
    # allows a few words in between: "100% T/T in advance", "30% by wire as deposit"
    adv = re.search(r"(\d{1,3})\s*%\s*(?:[A-Za-z/]+\s+){0,3}?(?:advance|upfront|pre-?payment|deposit|down\s*payment)", text, re.I)
    advance = float(adv.group(1)) if adv else 0.0
    if not adv and re.search(r"(?:payment|paid|payable)\s+(?:fully\s+)?in\s+advance|full\s+advance", text, re.I):
        advance = 100.0
    net = re.search(r"\bnet\s*(\d{1,3})\b", text, re.I)
    return advance, int(net.group(1)) if net else 0


_BULLET_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
WRAP_WIDTH = 80  # a line at least this long with no closing punctuation was wrapped


def _reflow(lines: list[str], skip: set[int]) -> list[str]:
    """Rejoin sentences that a PDF or email client wrapped across lines."""
    out: list[str] = []
    open_line = False
    for i, raw in enumerate(lines):
        line = raw.strip()
        tabular = i in skip or "|" in line or len(re.findall(r"\s{3,}", line)) >= 2
        if not line or tabular:
            open_line = False
            if line and i not in skip:
                out.append(line)
            continue
        if open_line and not _BULLET_RE.match(raw):
            out[-1] += " " + line
        else:
            out.append(line)
        open_line = len(line) >= WRAP_WIDTH and not re.search(r"[.:;!?]$", line)
    return out


def _segments(lines: list[str], skip: set[int]) -> list[str]:
    segs = []
    for line in _reflow(lines, skip):
        line = _BULLET_RE.sub("", line).strip()
        segs.extend(s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z])", line) if s.strip())
    return segs


def _fees(segments: list[str], quantity: float = 0) -> list[Fee]:
    fees: list[Fee] = []
    for seg in segments:
        kinds = sorted(((m.start(), k) for k, rx in FEE_RES.items() if (m := rx.search(seg))))
        if not kinds:
            continue
        excluded = bool(EXCLUDED_RE.search(NO_EXTRA_RE.sub("", seg)))
        if INCLUDED_RE.search(seg) and not excluded:
            continue
        money, pct = _money(seg), PERCENT_RE.search(seg)
        per_n = re.search(rf"(?:{CUR})\s*(?:{NUM})\s*(?:per|/)\s*{PER_N}|(?:{NUM})\s*(?:USD|EUR|GBP|INR|CNY|TRY)\s*(?:per|/)\s*{PER_N}", seg, re.I)
        if money and per_n and quantity:
            # a charge quoted per 100 / per 1000 pieces scales with the order quantity
            basis = _basis(per_n.group(1) or per_n.group(2))
            fees.append(Fee(kind=kinds[0][1], basis="fixed", amount=round(money[0][0] * quantity / basis, 2), evidence=seg, per_quantity=quantity))
        elif money:
            fees.append(Fee(kind=kinds[0][1], basis="fixed", amount=money[0][0], evidence=seg))
        elif pct:
            fees.append(Fee(kind=kinds[0][1], basis="percent_of_goods", percent=float(pct.group(1)), evidence=seg))
        elif excluded:
            fees.extend(Fee(kind=k, basis="unquantified", evidence=seg) for _, k in kinds)
    return fees


def parse_quote(text: str, source_file: str = "") -> ParsedQuote:
    lines = text.splitlines()
    warnings: list[str] = []

    items, table_lines = _table_rows(lines)
    if not items:
        items, table_lines = _vertical_rows(lines)
    if not items and (single := _single_item(text)):
        items = [single]

    # Table rows that are really charges (e.g. "Export packaging | 1 | 350.00 | 350.00")
    fees: list[Fee] = []
    goods: list[LineItem] = []
    for it in items:
        kind = next((k for k, rx in FEE_RES.items() if rx.search(it.description)), None)
        if kind and len(items) > 1:
            fees.append(Fee(kind=kind, basis="fixed", amount=it.quantity * it.unit_price, evidence=it.description))
        else:
            goods.append(it)

    fees += _fees(_segments(lines, table_lines), sum(li.quantity for li in goods))
    # Terms are read from re-flowed text, so a clause a PDF wrapped mid-sentence
    # ("Dispatch\nwithin 15 days") is still seen as one sentence.
    flow = "\n".join(_reflow(lines, set()))
    advance, net = _payment(flow)

    def first_int(pattern: str) -> int | None:
        m = re.search(pattern, flow, re.I)
        return int(m.group(1).replace(",", "")) if m else None

    warranty = re.search(r"warranty\b[^.\n\d]{0,20}(\d+)\s*-?\s*(month|year)", flow, re.I) or re.search(
        r"(\d+)\s*-?\s*(month|year)s?\s+warranty", flow, re.I
    )
    escalation = next((s for s in _segments(lines, set()) if ESCALATION_RE.search(s)), None)
    incoterm = INCOTERM_RE.search(text)

    quote = ParsedQuote(
        source_file=source_file,
        supplier_name=_supplier(lines),
        currency=_currency(text),
        line_items=goods,
        quoted_total=_quoted_total(text),
        lead_time_days=_lead_time(flow),
        incoterm=incoterm.group(1) if incoterm else None,
        advance_payment_pct=advance,
        net_payment_days=net,
        moq=first_int(r"(?:\bMOQ|minimum order(?: quantity)?)\b[^.\n\d]{0,20}([\d,]+)"),
        validity_days=first_int(r"valid(?:ity)?\b[^.\n\d]{0,40}(\d+)\s*days"),
        warranty_months=int(warranty.group(1)) * (12 if warranty.group(2).lower() == "year" else 1) if warranty else None,
        price_escalation_clause=escalation,
        fees=fees,
    )
    if not quote.line_items:
        warnings.append("Could not find a unit price and quantity.")
    if quote.lead_time_days is None:
        warnings.append("No delivery lead time stated.")
    if quote.incoterm is None:
        warnings.append("No Incoterm stated; delivery responsibility is ambiguous.")
    quote.parse_warnings = warnings
    return quote


def is_usable(quote: ParsedQuote) -> bool:
    return bool(quote.supplier_name and quote.line_items)
