"""Generate a synthetic supplier base and ~3 years of purchase-order history.

Run:  python -m app.seed

Each supplier has hidden traits (typical lead time, how much buffer it quotes, how
variable it is). Lateness is *simulated from those traits*, never assigned directly,
so the risk model has real structure to learn rather than a label to memorise.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
from sqlalchemy import insert

from app.db import Base, PurchaseOrder, Supplier, engine, session_scope

CATEGORY_BASE_PRICE = {"servo_motor": 44.0, "gearbox": 180.0, "bearing": 6.5, "controller": 320.0, "fastener": 0.115, "nut": 0.032, "washer": 0.011}

# Two supplier groups, each with its own product mix and typical order size.
# name, country, km to buyer, years, certs, typical lead (days, surface), quoted buffer,
# variability, price factor, defect rate, relative order volume
MOTION_SUPPLIERS = [
    ("Shenzhen Dynatek Motion Co. Ltd", "China", 11600, 9, "ISO 9001", 38, 1.06, 0.22, 0.90, 0.018, 1.2),
    ("Bavaria Antriebstechnik GmbH", "Germany", 7000, 31, "ISO 9001, ISO 14001", 20, 1.15, 0.08, 1.08, 0.004, 1.0),
    ("Midwest Precision Drives Inc.", "United States", 900, 22, "ISO 9001, UL", 12, 1.08, 0.10, 1.09, 0.006, 1.1),
    ("Anatolia Elektrik Motor A.S.", "Turkey", 9000, 14, "ISO 9001, CE", 27, 1.10, 0.16, 0.94, 0.012, 0.8),
    ("Pune Rotodyne Pvt Ltd", "India", 12800, 4, "ISO 9001", 36, 1.02, 0.28, 0.91, 0.030, 0.4),
    ("Osaka Seimitsu KK", "Japan", 10200, 40, "ISO 9001, ISO 14001", 26, 1.14, 0.07, 1.15, 0.003, 0.7),
    ("Guadalajara Motores SA de CV", "Mexico", 2600, 12, "ISO 9001", 14, 1.05, 0.15, 0.97, 0.014, 0.9),
    ("Lombardia Motori SpA", "Italy", 7400, 27, "ISO 9001, CE", 24, 1.07, 0.14, 1.04, 0.008, 0.6),
    ("Hanoi Drive Tech JSC", "Vietnam", 13200, 6, "ISO 9001", 40, 1.03, 0.24, 0.88, 0.022, 0.6),
    ("Ontario Motion Systems Ltd", "Canada", 1100, 18, "ISO 9001, CSA", 11, 1.12, 0.09, 1.07, 0.007, 0.7),
]
FASTENER_SUPPLIERS = [
    ("Ningbo Eastbolt Fastener Co. Ltd", "China", 11400, 11, "ISO 9001", 40, 1.05, 0.20, 0.82, 0.020, 1.2),
    ("Ludhiana Forge & Fasteners Pvt Ltd", "India", 11900, 7, "ISO 9001", 42, 1.03, 0.26, 0.85, 0.028, 0.6),
    ("Rheinland Schraubenwerk GmbH", "Germany", 6700, 58, "ISO 9001, IATF 16949", 22, 1.14, 0.08, 1.18, 0.003, 0.8),
    ("Great Lakes Bolt & Screw Co.", "United States", 600, 35, "ISO 9001", 9, 1.10, 0.10, 1.12, 0.005, 1.1),
    ("Kaohsiung Precision Fasteners Corp", "Taiwan", 12300, 24, "ISO 9001, IATF 16949", 34, 1.12, 0.12, 0.92, 0.007, 1.0),
    ("Monterrey Tornillos Industriales SA de CV", "Mexico", 2300, 16, "ISO 9001", 12, 1.06, 0.14, 0.98, 0.012, 0.8),
]
SUPPLIERS = MOTION_SUPPLIERS + FASTENER_SUPPLIERS

# (suppliers, categories, category mix, typical order quantity, number of orders)
GROUPS = [
    (MOTION_SUPPLIERS, ["servo_motor", "gearbox", "bearing", "controller"], [0.4, 0.2, 0.25, 0.15], 400, 4000),
    (FASTENER_SUPPLIERS, ["fastener", "nut", "washer"], [0.5, 0.25, 0.25], 40000, 4400),
]
HISTORY_DAYS = 3 * 365


def generate(seed: int = 42) -> tuple[list[dict], list[dict]]:
    rng = np.random.default_rng(seed)
    today = date.today()
    suppliers = [
        {"id": i + 1, "name": s[0], "country": s[1], "distance_km": float(s[2]), "years_active": s[3], "certifications": s[4]}
        for i, s in enumerate(SUPPLIERS)
    ]
    orders: list[dict] = []
    offset = 0
    for group, categories, mix, typical_qty, n_orders in GROUPS:
        orders += _simulate(rng, today, group, offset, categories, mix, typical_qty, n_orders)
        offset += len(group)
    return suppliers, orders


def _simulate(rng, today, group, id_offset, categories, mix, typical_qty, n_orders) -> list[dict]:
    volume = np.array([s[10] for s in group])
    orders: list[dict] = []
    for _ in range(n_orders):
        idx = int(rng.choice(len(group), p=volume / volume.sum()))
        _, country, km, _, _, lead_base, buffer, sigma, price_factor, defect, _ = group[idx]

        days_ago = int(rng.integers(1, HISTORY_DAYS))
        order_date = today - timedelta(days=days_ago)
        category = categories[int(rng.choice(len(categories), p=mix))]
        quantity = int(np.clip(rng.lognormal(np.log(typical_qty), 0.8), typical_qty / 20, typical_qty * 50))

        if km < 3500:
            mode, mode_factor = "road", 1.0
        elif rng.random() < 0.2:
            mode, mode_factor = "air", 0.45
        else:
            mode, mode_factor = "sea", 1.0

        size_effect = 1 + 0.08 * np.clip(np.log10(quantity / (typical_qty * 1.25)), -1, 2)
        season = 1.0
        if order_date.month in (1, 2) and country in ("China", "Vietnam", "Taiwan"):
            season *= 1.20  # Lunar New Year shutdowns
        if order_date.month in (11, 12):
            season *= 1.08  # peak-season freight congestion

        expected = lead_base * mode_factor
        promised = max(2, round(expected * buffer * rng.lognormal(0, 0.08)))
        actual = max(1, round(expected * size_effect * season * rng.lognormal(0, sigma)))

        years_in = (HISTORY_DAYS - days_ago) / 365
        unit_price = float(
            CATEGORY_BASE_PRICE[category]
            * price_factor
            * (1 + 0.03 * years_in)
            * (1 - 0.03 * np.log10(quantity / (typical_qty / 4)))
            * rng.lognormal(0, 0.03)
        )

        orders.append(
            {
                "supplier_id": id_offset + idx + 1,
                "category": category,
                "order_date": order_date,
                "quantity": quantity,
                "unit_price_usd": round(unit_price, 2 if unit_price >= 1 else 4),
                "shipping_mode": mode,
                "promised_lead_days": int(promised),
                "actual_lead_days": int(actual),
                "late": bool(actual > promised + 1),
                "defect_rate": round(float(rng.beta(2, 2 / defect)), 4),
            }
        )
    return orders


def seed(reset: bool = True) -> None:
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    suppliers, orders = generate()
    with session_scope() as s:
        s.execute(insert(Supplier), suppliers)
        s.execute(insert(PurchaseOrder), orders)
    late = sum(o["late"] for o in orders) / len(orders)
    print(f"Seeded {len(suppliers)} suppliers and {len(orders)} purchase orders (overall late rate {late:.1%}).")


if __name__ == "__main__":
    seed()
