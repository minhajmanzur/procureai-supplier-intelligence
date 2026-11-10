"""Serve delivery-risk predictions with per-prediction explanations."""
from __future__ import annotations

import json
from functools import lru_cache

import pandas as pd
import xgboost as xgb

from app.config import settings
from app.ml.features import FEATURE_LABELS, FEATURES
from app.ml.train import META_FILE, MODEL_FILE


class ModelNotTrained(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _load() -> tuple[xgb.Booster, dict]:
    path = settings.model_dir / MODEL_FILE
    if not path.exists():
        raise ModelNotTrained("Delivery-risk model not found. Run `python -m app.ml.train`.")
    booster = xgb.Booster()
    booster.load_model(path)
    meta = json.loads((settings.model_dir / META_FILE).read_text())
    return booster, meta


def model_meta() -> dict:
    return _load()[1]


def predict_late_probability(row: dict) -> dict:
    """Return P(late) plus the features pushing the prediction up or down the most."""
    booster, _ = _load()
    dm = xgb.DMatrix(pd.DataFrame([row], columns=FEATURES).astype(float))
    p = float(booster.predict(dm)[0])
    contribs = booster.predict(dm, pred_contribs=True)[0][:-1]  # last column is the bias term
    ranked = sorted(zip(FEATURES, contribs), key=lambda kv: -abs(kv[1]))[:4]
    drivers = [
        {
            "feature": name,
            "label": FEATURE_LABELS[name],
            "value": None if pd.isna(row[name]) else round(float(row[name]), 3),
            "effect": "raises risk" if c > 0 else "lowers risk",
            "log_odds": round(float(c), 3),
        }
        for name, c in ranked
        if abs(c) > 0.02
    ]
    return {"p_late": p, "drivers": drivers}
