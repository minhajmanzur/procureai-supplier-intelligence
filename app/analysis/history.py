"""Supplier performance over time, for the supplier dashboard."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


def _recent(orders: pd.DataFrame, days: int = 365) -> pd.DataFrame:
    return orders[orders["order_date"] >= pd.Timestamp(date.today() - timedelta(days=days))]


def _rate(series: pd.Series) -> float | None:
    return float(1 - series.mean()) if len(series) else None


def overview(orders: pd.DataFrame, suppliers: list[dict]) -> dict:
    """Portfolio totals plus one summary row per supplier."""
    if orders.empty:
        return {"totals": {}, "categories": [], "suppliers": []}
    orders = orders.assign(spend=orders["quantity"] * orders["unit_price_usd"])
    last, before = _recent(orders), orders[orders["order_date"] < pd.Timestamp(date.today() - timedelta(days=365))]

    rows = []
    for s in suppliers:
        h = orders[orders["supplier_id"] == s["id"]]
        if h.empty:
            continue
        h_last, h_before = _recent(h), h[~h.index.isin(_recent(h).index)]
        late = h[h["late"] == 1]
        rows.append(
            {
                **s,
                "categories": sorted(h["category"].unique()),
                "orders": int(len(h)),
                "orders_12m": int(len(h_last)),
                "spend_12m_usd": float(h_last["spend"].sum()),
                "on_time_rate": _rate(h["late"]),
                "on_time_rate_12m": _rate(h_last["late"]),
                "on_time_rate_before": _rate(h_before["late"]),
                "avg_days_late_when_late": float((late["actual_lead_days"] - late["promised_lead_days"]).mean()) if len(late) else 0.0,
                "avg_defect_rate": float(h["defect_rate"].mean()),
                "median_lead_days": float(h["actual_lead_days"].median()),
            }
        )
    return {
        "totals": {
            "suppliers": len(rows),
            "orders": int(len(orders)),
            "orders_12m": int(len(last)),
            "on_time_rate_12m": _rate(last["late"]),
            "on_time_rate_before": _rate(before["late"]),
            "spend_12m_usd": float(last["spend"].sum()),
            "avg_defect_rate_12m": float(last["defect_rate"].mean()) if len(last) else None,
        },
        "categories": sorted(orders["category"].unique()),
        "suppliers": rows,
    }


def supplier_history(orders: pd.DataFrame, supplier_id: int) -> dict:
    """Quarterly delivery performance, price trend per category and recent orders."""
    h = orders[orders["supplier_id"] == supplier_id].copy()
    if h.empty:
        return {"quarters": [], "prices": {}, "by_mode": [], "recent_orders": []}
    h["period"] = h["order_date"].dt.to_period("Q")
    periods = pd.period_range(h["period"].min(), h["period"].max(), freq="Q")

    def label(p: pd.Period) -> str:
        return f"{p.year} Q{p.quarter}"

    grouped = h.groupby("period")
    q = pd.DataFrame(
        {
            "orders": grouped.size(),
            "on_time_rate": 1 - grouped["late"].mean(),
            "promised_lead_days": grouped["promised_lead_days"].mean(),
            "actual_lead_days": grouped["actual_lead_days"].mean(),
            "defect_rate": grouped["defect_rate"].mean(),
        }
    ).reindex(periods)
    quarters = [
        {
            "period": label(p),
            "orders": int(r["orders"]) if pd.notna(r["orders"]) else 0,
            **{k: (round(float(r[k]), 4) if pd.notna(r[k]) else None) for k in ("on_time_rate", "promised_lead_days", "actual_lead_days", "defect_rate")},
        }
        for p, r in q.iterrows()
    ]

    prices = {}
    for category, c in h.groupby("category"):
        med = c.groupby("period")["unit_price_usd"].median().reindex(periods)
        prices[category] = [{"period": label(p), "median_price_usd": (round(float(v), 4) if pd.notna(v) else None)} for p, v in med.items()]

    by_mode = [
        {
            "mode": mode,
            "orders": int(len(m)),
            "on_time_rate": _rate(m["late"]),
            "median_promised_days": float(m["promised_lead_days"].median()),
            "median_actual_days": float(m["actual_lead_days"].median()),
        }
        for mode, m in h.groupby("shipping_mode")
    ]
    recent = h.sort_values("order_date", ascending=False).head(12)
    recent_orders = [
        {
            "order_date": r.order_date.date().isoformat(),
            "category": r.category,
            "quantity": int(r.quantity),
            "unit_price_usd": float(r.unit_price_usd),
            "shipping_mode": r.shipping_mode,
            "promised_lead_days": int(r.promised_lead_days),
            "actual_lead_days": int(r.actual_lead_days),
            "late": bool(r.late),
        }
        for r in recent.itertuples()
    ]
    return {"quarters": quarters, "prices": prices, "by_mode": by_mode, "recent_orders": recent_orders}
