"""Optional Claude layer. The pipeline is fully functional without it.

Used for two things when credentials are available:
  1. extraction fallback - reading a quotation the rule-based parser could not handle
  2. narrative           - an executive summary written from the computed numbers
The numbers themselves always come from the deterministic cost model and XGBoost.
"""
from __future__ import annotations

import json
import logging
import os

from app.config import settings
from app.schemas import ParsedQuote

log = logging.getLogger(__name__)

EXTRACTION_SYSTEM = """You extract structured data from supplier quotations for a procurement team.

Report only what the document states. Leave a field null rather than guessing.
Fees are any charge beyond the unit price: freight, insurance, duty, handling, packaging,
tooling, documentation, surcharges, bank charges. For each one, copy the sentence that
states it into `evidence`. If the quote says the buyer pays for something without giving
an amount, record it with basis "unquantified". Do not list charges the quote says are
included in the price. Line items are goods only, never fees."""

NARRATIVE_SYSTEM = """You are a senior procurement analyst writing for a purchasing manager who will
sign the order. You are given the full output of a quotation comparison: landed costs,
predicted delivery risk, pricing checks and a scored ranking.

Write a short recommendation memo in plain prose, at most 200 words: which supplier to
award and why, what the cheapest-looking quote really costs, and what to settle with the
recommended supplier before signing. Use only the figures provided; never invent numbers.
No headings, no bullet points."""


def get_client():
    if not settings.llm_enabled or not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        return None
    import anthropic

    return anthropic.Anthropic()


def extract_quote(text: str, source_file: str) -> ParsedQuote | None:
    client = get_client()
    if client is None:
        return None
    import anthropic

    try:
        response = client.messages.parse(
            model=settings.llm_model,
            max_tokens=16000,
            system=EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": f"<quotation>\n{text}\n</quotation>"}],
            output_format=ParsedQuote,
        )
    except anthropic.APIError as exc:
        log.warning("LLM extraction failed for %s: %s", source_file, exc)
        return None
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return None
    quote = response.parsed_output
    quote.source_file = source_file
    quote.parse_method = "llm"
    return quote


def narrate(summary: dict) -> str | None:
    client = get_client()
    if client is None:
        return None
    import anthropic

    try:
        response = client.beta.messages.create(
            model=settings.llm_model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=NARRATIVE_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(summary, default=str)}],
        )
    except (anthropic.APIError, TypeError) as exc:
        log.warning("LLM narrative failed: %s", exc)
        return None
    if response.stop_reason == "refusal":
        return None
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return text or None
