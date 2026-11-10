"""Match the lines of a quotation to the lines of the RFQ.

Suppliers describe the same part in their own words ("Hex cap screw M10x50 cl. 8.8 Zn"
for "Hex bolt M10x50 DIN 933 grade 8.8 zinc plated"), reorder lines, skip items they
do not stock and add ones nobody asked for. Matching is by product type first, then
by shared specification tokens (size, standard, grade).
"""
from __future__ import annotations

import re

# words that name the same thing
SYNONYMS = {"screw": "bolt", "screws": "bolt", "bolts": "bolt", "nuts": "nut", "washers": "washer", "motors": "motor",
            "zn": "zinc", "gr": "grade", "cl": "grade", "class": "grade", "bearings": "bearing", "gearboxes": "gearbox"}
PRODUCT_TYPES = {"bolt", "nut", "washer", "stud", "rivet", "motor", "gearbox", "bearing", "controller"}
NOISE = {"the", "of", "and", "or", "with", "type", "model", "equivalent", "cap", "pcs"}
MIN_SCORE = 0.3


def tokens(text: str) -> set[str]:
    out: set[str] = set()
    for raw in re.findall(r"[a-z]*\d+(?:[x.]\d+)*[a-z]*|[a-z]+", text.lower()):
        tok = SYNONYMS.get(raw, raw)
        if tok in NOISE:
            continue
        out.add(tok)
        if size := re.match(r"(m\d+)x\d+", tok):  # "m10x50" also says "m10"
            out.add(size.group(1))
    return out


def similarity(requested: str, quoted: str) -> float:
    a, b = tokens(requested), tokens(quoted)
    type_a, type_b = a & PRODUCT_TYPES, b & PRODUCT_TYPES
    if type_a and type_b and not (type_a & type_b):
        return 0.0  # a nut is never a match for a bolt, however similar the rest
    if not a:
        return 0.0
    overlap = len(a & b) / len(a)
    return overlap + (0.5 if type_a & type_b else 0.0)


def match_lines(requested: list[str], quoted: list[str]) -> tuple[list[int | None], list[int]]:
    """Return (for each requested line, the index of the quoted line or None; unrequested quoted lines)."""
    if len(requested) == 1 and len(quoted) == 1:
        return [0], []  # one item asked, one item quoted: no judgement needed
    pairs = sorted(((similarity(r, q), i, j) for i, r in enumerate(requested) for j, q in enumerate(quoted)), reverse=True)
    assignment: list[int | None] = [None] * len(requested)
    taken: set[int] = set()
    for score, i, j in pairs:
        if score < MIN_SCORE:
            break
        if assignment[i] is None and j not in taken:
            assignment[i] = j
            taken.add(j)
    if len(requested) == 1 and assignment[0] is None and quoted:
        assignment[0] = 0  # single-item RFQ: take the first goods line rather than reject the quote
        taken.add(0)
    return assignment, [j for j in range(len(quoted)) if j not in taken]
