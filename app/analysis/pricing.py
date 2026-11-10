"""Detect unusual pricing by checking each quote against three references:
the other quotes in this round, the 12-month market history for the category,
and the supplier's own recent prices.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.analysis.fmt import price as fmt

PEER_THRESHOLD = 0.20
OWN_HISTORY_THRESHOLD = 0.15
MARKET_MARGIN = 0.05  # how far beyond the 5th/95th percentile counts as unusual


def market_reference(orders: pd.DataFrame, category: str | None) -> dict | None:
    if orders.empty or not category:
        return None
    cutoff = pd.Timestamp(date.today() - timedelta(days=365))
    recent = orders[(orders["category"] == category) & (orders["order_date"] >= cutoff)]["unit_price_usd"]
    if len(recent) < 30:
        return None
    p05, p50, p95 = np.percentile(recent, [5, 50, 95])
    return {"orders": int(len(recent)), "p05": float(p05), "median": float(p50), "p95": float(p95)}


def audit_prices(unit_prices: dict[str, float], market: dict | None, own_medians: dict[str, float | None]) -> dict[str, dict]:
    """unit_prices: quote id -> unit price in base currency. Returns per-quote findings."""
    peer_median = float(np.median(list(unit_prices.values()))) if unit_prices else 0.0
    out: dict[str, dict] = {}
    for qid, price in unit_prices.items():
        signals: list[str] = []
        direction = None

        vs_peers = price / peer_median - 1 if peer_median else 0.0
        if len(unit_prices) >= 3 and abs(vs_peers) > PEER_THRESHOLD:
            direction = "low" if vs_peers < 0 else "high"
            signals.append(f"{abs(vs_peers):.0%} {'below' if vs_peers < 0 else 'above'} the median of this round's quotes ({fmt(peer_median)})")

        if market:
            if price < market["p05"] * (1 - MARKET_MARGIN):
                direction = direction or "low"
                signals.append(f"below the 5th percentile of {market['orders']} market orders in the last 12 months ({fmt(market['p05'])})")
            elif price > market["p95"] * (1 + MARKET_MARGIN):
                direction = direction or "high"
                signals.append(f"above the 95th percentile of {market['orders']} market orders in the last 12 months ({fmt(market['p95'])})")

        own = own_medians.get(qid)
        vs_own = price / own - 1 if own else None
        if len(signals) == 1 and "this round" in signals[0] and vs_own is not None and abs(vs_own) <= OWN_HISTORY_THRESHOLD:
            # Far from the other quotes, but normal for the market and for this supplier:
            # that is a supplier-mix effect (e.g. domestic premium), not an anomaly.
            signals, direction = [], None
        if vs_own is not None and abs(vs_own) > OWN_HISTORY_THRESHOLD:
            direction = direction or ("low" if vs_own < 0 else "high")
            signals.append(f"{abs(vs_own):.0%} {'below' if vs_own < 0 else 'above'} this supplier's own 12-month median ({fmt(own)})")

        severity = "none" if not signals else "high" if len(signals) >= 2 else "medium"
        out[qid] = {
            "unit_price_usd": round(price, 5),
            "vs_peer_median": round(vs_peers, 4),
            "vs_market_median": round(price / market["median"] - 1, 4) if market else None,
            "vs_own_history": round(vs_own, 4) if vs_own is not None else None,
            "anomaly": bool(signals),
            "direction": direction,
            "severity": severity,
            "signals": signals,
        }
    return out
