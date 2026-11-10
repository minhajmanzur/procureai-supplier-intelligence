"""Feature engineering for the delivery-risk model.

The same feature definitions are used for training (point-in-time, built only from
orders placed *before* each row to avoid leakage) and for scoring a new quotation
(built from the supplier's full history).
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.db import PurchaseOrder, Supplier

FEATURES = [
    "promised_lead_days",
    "quantity",
    "order_value_usd",
    "distance_km",
    "mode_sea",
    "mode_air",
    "month",
    "sup_years_active",
    "sup_prior_orders",
    "sup_prior_late_rate",
    "sup_prior_avg_overrun",
    "sup_prior_median_lead",
    "lead_ratio",
]

FEATURE_LABELS = {
    "promised_lead_days": "promised lead time",
    "quantity": "order quantity",
    "order_value_usd": "order value",
    "distance_km": "shipping distance",
    "mode_sea": "sea freight",
    "mode_air": "air freight",
    "month": "time of year",
    "sup_years_active": "supplier track-record length",
    "sup_prior_orders": "number of past orders",
    "sup_prior_late_rate": "supplier's historical late rate",
    "sup_prior_avg_overrun": "supplier's historical schedule overrun",
    "sup_prior_median_lead": "supplier's typical actual lead time",
    "lead_ratio": "promised lead time vs. supplier's typical lead time",
}


def load_orders(session) -> pd.DataFrame:
    stmt = select(
        PurchaseOrder.id,
        PurchaseOrder.supplier_id,
        Supplier.name.label("supplier_name"),
        Supplier.country,
        Supplier.distance_km,
        Supplier.years_active.label("sup_years_active"),
        PurchaseOrder.category,
        PurchaseOrder.order_date,
        PurchaseOrder.quantity,
        PurchaseOrder.unit_price_usd,
        PurchaseOrder.shipping_mode,
        PurchaseOrder.promised_lead_days,
        PurchaseOrder.actual_lead_days,
        PurchaseOrder.late,
        PurchaseOrder.defect_rate,
    ).join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
    df = pd.DataFrame(session.execute(stmt).mappings().all())
    if df.empty:
        return df
    df["order_date"] = pd.to_datetime(df["order_date"])
    df["late"] = df["late"].astype(int)
    df["overrun"] = (df["actual_lead_days"] - df["promised_lead_days"]).clip(lower=0) / df["promised_lead_days"]
    return df


def build_training_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["order_date", "id"]).reset_index(drop=True)
    g = df.groupby("supplier_id", sort=False)
    df["sup_prior_orders"] = g.cumcount()
    prior = df["sup_prior_orders"].replace(0, np.nan)
    df["sup_prior_late_rate"] = (g["late"].cumsum() - df["late"]) / prior
    df["sup_prior_avg_overrun"] = (g["overrun"].cumsum() - df["overrun"]) / prior
    df["sup_prior_median_lead"] = g["actual_lead_days"].transform(lambda s: s.shift().expanding().median())
    return _derive(df)


def _derive(df: pd.DataFrame) -> pd.DataFrame:
    df["order_value_usd"] = df["quantity"] * df["unit_price_usd"]
    df["mode_sea"] = (df["shipping_mode"] == "sea").astype(int)
    df["mode_air"] = (df["shipping_mode"] == "air").astype(int)
    df["month"] = pd.to_datetime(df["order_date"]).dt.month
    df["lead_ratio"] = df["promised_lead_days"] / df["sup_prior_median_lead"]
    return df


def infer_shipping_mode(distance_km: float | None, lead_days: float | None) -> str:
    if distance_km is None or distance_km < 3500:
        return "road"
    return "air" if (lead_days is not None and lead_days < 18) else "sea"


def supplier_profile(df: pd.DataFrame, supplier_id: int, category: str | None = None) -> dict:
    """Reliability snapshot from a supplier's full order history."""
    h = df[df["supplier_id"] == supplier_id]
    if h.empty:
        return {"orders": 0}
    late = h[h["late"] == 1]
    cutoff = pd.Timestamp(date.today() - timedelta(days=365))
    recent, older = h[h["order_date"] >= cutoff], h[h["order_date"] < cutoff]
    prices = h[h["category"] == category] if category else h.iloc[0:0]
    recent_prices = prices[prices["order_date"] >= cutoff]
    by_mode = h.groupby("shipping_mode")["actual_lead_days"].median().round(1).to_dict()
    return {
        "orders": int(len(h)),
        "on_time_rate": float(1 - h["late"].mean()),
        "on_time_rate_last_12m": float(1 - recent["late"].mean()) if len(recent) else None,
        "on_time_rate_before": float(1 - older["late"].mean()) if len(older) else None,
        "avg_days_late_when_late": float((late["actual_lead_days"] - late["promised_lead_days"]).mean()) if len(late) else 0.0,
        "avg_overrun": float(h["overrun"].mean()),
        "median_actual_lead_days": float(h["actual_lead_days"].median()),
        "median_actual_lead_days_by_mode": by_mode,
        "avg_defect_rate": float(h["defect_rate"].mean()),
        "category_orders": int(len(prices)),
        "category_median_price_usd_12m": float(recent_prices["unit_price_usd"].median()) if len(recent_prices) >= 5 else None,
    }


def scoring_row(
    *,
    promised_lead_days: float,
    quantity: float,
    order_value_usd: float,
    distance_km: float | None,
    years_active: float | None,
    profile: dict,
) -> dict:
    """Feature row for a new quotation. Unknown suppliers get NaN history (XGBoost handles it)."""
    known = profile.get("orders", 0) > 0
    mode = infer_shipping_mode(distance_km, promised_lead_days)
    median_lead = profile.get("median_actual_lead_days") if known else np.nan
    return {
        "promised_lead_days": promised_lead_days,
        "quantity": quantity,
        "order_value_usd": order_value_usd,
        "distance_km": distance_km if distance_km is not None else np.nan,
        "mode_sea": int(mode == "sea"),
        "mode_air": int(mode == "air"),
        "month": date.today().month,
        "sup_years_active": years_active if years_active is not None else np.nan,
        "sup_prior_orders": profile.get("orders", 0),
        "sup_prior_late_rate": 1 - profile["on_time_rate"] if known else np.nan,
        "sup_prior_avg_overrun": profile["avg_overrun"] if known else np.nan,
        "sup_prior_median_lead": median_lead,
        "lead_ratio": promised_lead_days / median_lead if known else np.nan,
    }
