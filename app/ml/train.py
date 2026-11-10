"""Train the XGBoost late-delivery classifier.

Run:  python -m app.ml.train

Evaluation uses a time-based split (train on the oldest 80% of orders, test on the
newest 20%), which is how the model is actually used: predicting future orders.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from xgboost import XGBClassifier

from app.config import settings
from app.db import session_scope
from app.ml.features import FEATURES, build_training_frame, load_orders

MODEL_FILE = "delivery_risk.json"
META_FILE = "delivery_risk_meta.json"


def train() -> dict:
    with session_scope() as s:
        raw = load_orders(s)
    if raw.empty:
        raise SystemExit("No purchase orders in the database. Run `python -m app.seed` first.")

    df = build_training_frame(raw)
    split = int(len(df) * 0.8)
    train_df, test_df = df.iloc[:split], df.iloc[split:]

    params = dict(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=5,
        reg_lambda=2.0,
        eval_metric="logloss",
        random_state=42,
    )
    model = XGBClassifier(**params)
    model.fit(train_df[FEATURES], train_df["late"])

    proba = model.predict_proba(test_df[FEATURES])[:, 1]
    base_rate = float(train_df["late"].mean())
    metrics = {
        "test_rows": int(len(test_df)),
        "train_rows": int(len(train_df)),
        "late_rate_train": base_rate,
        "late_rate_test": float(test_df["late"].mean()),
        "roc_auc": float(roc_auc_score(test_df["late"], proba)),
        "pr_auc": float(average_precision_score(test_df["late"], proba)),
        "brier": float(brier_score_loss(test_df["late"], proba)),
        "brier_baseline": float(brier_score_loss(test_df["late"], np.full(len(test_df), base_rate))),
    }

    # Refit on all data for serving; the held-out metrics above describe the approach.
    final = XGBClassifier(**params)
    final.fit(df[FEATURES], df["late"])

    settings.model_dir.mkdir(parents=True, exist_ok=True)
    final.save_model(settings.model_dir / MODEL_FILE)
    importance = dict(zip(FEATURES, (float(v) for v in final.feature_importances_)))
    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "features": FEATURES,
        "metrics": metrics,
        "feature_importance": dict(sorted(importance.items(), key=lambda kv: -kv[1])),
    }
    (settings.model_dir / META_FILE).write_text(json.dumps(meta, indent=2))
    return meta


if __name__ == "__main__":
    m = train()["metrics"]
    print(
        f"Trained on {m['train_rows']} orders, tested on the newest {m['test_rows']}.\n"
        f"  ROC AUC {m['roc_auc']:.3f} | PR AUC {m['pr_auc']:.3f} (late rate {m['late_rate_test']:.1%})\n"
        f"  Brier {m['brier']:.4f} vs. {m['brier_baseline']:.4f} for always predicting the base rate"
    )
