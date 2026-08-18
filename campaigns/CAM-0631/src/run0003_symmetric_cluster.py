from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from run0002_toxicity_signal import (
    HORIZONS,
    SESSIONS,
    SYMBOLS,
    TRAIN_SESSIONS,
    VALIDATION_SESSIONS,
    WINDOWS,
    auc_score,
    build_events,
)


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0631" / "artifacts" / "RUN-0003"
BASE = ["spread_bps", "side_size_imbalance", "quote_rate_1s", "side_mid_return_1s", "side_mid_return_5s", "side_trade_imbalance_1s"]
MODELS = {
    "base": BASE,
    "without_trade_flow": [column for column in BASE if column != "side_trade_imbalance_1s"],
    "without_local_returns": [column for column in BASE if column not in {"side_mid_return_1s", "side_mid_return_5s"}],
}
TARGET_HORIZONS = [1, 5, 15]


def weighted_logit_irls(x: np.ndarray, y: np.ndarray, sample_weight: np.ndarray, ridge: float = 1.0, iterations: int = 50) -> np.ndarray:
    beta = np.zeros(x.shape[1])
    penalty = np.eye(x.shape[1]) * ridge
    penalty[0, 0] = 0.0
    for _ in range(iterations):
        eta = np.clip(x @ beta, -30, 30)
        p = 1.0 / (1.0 + np.exp(-eta))
        variance = np.maximum(p * (1 - p), 1e-6)
        w = variance * sample_weight
        z = eta + (y - p) / variance
        updated = np.linalg.solve(x.T @ (w[:, None] * x) + penalty, x.T @ (w * z))
        if np.max(np.abs(updated - beta)) < 1e-7:
            return updated
        beta = updated
    return beta


def clustered_data() -> pd.DataFrame:
    frames = []
    for symbol in SYMBOLS:
        for session in SESSIONS:
            for label, start, end in WINDOWS:
                frame = build_events(symbol, session, label, start, end)
                if len(frame):
                    frames.append(frame)
    events = pd.concat(frames, ignore_index=True)
    events["side_size_imbalance"] = events.side * events.size_imbalance
    events["side_mid_return_1s"] = events.side * events.mid_return_1s
    events["side_mid_return_5s"] = events.side * events.mid_return_5s
    events["side_trade_imbalance_1s"] = events.side * events.trade_imbalance_1s
    events["cluster_100ms"] = events.ts.dt.floor("100ms")
    numerical = list(dict.fromkeys(BASE + [f"markout_{h:g}s_bps" for h in HORIZONS]))
    clustered = events.groupby(["symbol", "session", "window", "cluster_100ms", "side"], as_index=False)[numerical].mean()
    return clustered


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = clustered_data()
    train = data[data.session.isin(TRAIN_SESSIONS)].copy()
    validation = data[data.session.isin(VALIDATION_SESSIONS)].copy()
    counts = train.groupby(["symbol", "session"]).size().rename("group_n")
    train = train.join(counts, on=["symbol", "session"])
    train["weight"] = 1.0 / train.group_n
    train["weight"] *= len(train) / train.weight.sum()
    summary_rows = []
    coefficient_rows = []
    prediction_frames = []
    for model_name, features in MODELS.items():
        mean = np.average(train[features], axis=0, weights=train.weight)
        variance = np.average((train[features].to_numpy() - mean) ** 2, axis=0, weights=train.weight)
        std = np.sqrt(np.maximum(variance, 1e-12))
        x = np.c_[np.ones(len(train)), (train[features].to_numpy() - mean) / std]
        xv = np.c_[np.ones(len(validation)), (validation[features].to_numpy() - mean) / std]
        for target_horizon in TARGET_HORIZONS:
            target = f"markout_{target_horizon:g}s_bps"
            y = (train[target].to_numpy() < 0).astype(float)
            yv = (validation[target].to_numpy() < 0).astype(int)
            beta = weighted_logit_irls(x, y, train.weight.to_numpy())
            probability = 1.0 / (1.0 + np.exp(-np.clip(xv @ beta, -30, 30)))
            low_cut, high_cut = np.quantile(probability, [0.2, 0.8])
            predicted = validation[["symbol", "session", "window", "cluster_100ms", "side"] + [f"markout_{h:g}s_bps" for h in HORIZONS]].copy()
            predicted["model"] = model_name
            predicted["target_horizon_s"] = target_horizon
            predicted["predicted_toxicity"] = probability
            predicted["bucket"] = np.where(probability <= low_cut, "low", np.where(probability >= high_cut, "high", "middle"))
            prediction_frames.append(predicted)
            for feature, value in zip(["intercept"] + features, beta):
                coefficient_rows.append({"model": model_name, "target_horizon_s": target_horizon, "feature": feature, "coefficient": value})
            for session, session_frame in predicted.groupby("session"):
                # Equal-symbol markout avoids letting quote-heavy names dominate the session.
                symbol_bucket = session_frame.groupby(["symbol", "bucket"])[[f"markout_{h:g}s_bps" for h in HORIZONS]].mean().reset_index()
                means = symbol_bucket.groupby("bucket")[[f"markout_{h:g}s_bps" for h in HORIZONS]].mean()
                low = means.loc["low"]
                high = means.loc["high"]
                per_symbol = symbol_bucket.pivot(index="symbol", columns="bucket", values=f"markout_{target_horizon:g}s_bps").dropna()
                summary_rows.append({
                    "model": model_name,
                    "target_horizon_s": target_horizon,
                    "session": session,
                    "auc": auc_score(yv[validation.session.eq(session).to_numpy()], probability[validation.session.eq(session).to_numpy()]),
                    "symbols_with_both_buckets": len(per_symbol),
                    "symbol_fraction_low_gt_high_at_target": float((per_symbol.low > per_symbol.high).mean()) if len(per_symbol) else np.nan,
                    **{f"low_markout_{h:g}s_bps": float(low[f"markout_{h:g}s_bps"]) for h in HORIZONS},
                    **{f"high_markout_{h:g}s_bps": float(high[f"markout_{h:g}s_bps"]) for h in HORIZONS},
                })
    summary = pd.DataFrame(summary_rows)
    predictions = pd.concat(prediction_frames, ignore_index=True)
    summary.to_csv(OUT / "model_validation_summary.csv", index=False)
    pd.DataFrame(coefficient_rows).to_csv(OUT / "model_coefficients.csv", index=False)
    # Base 5s predictions are enough for an auditable bounded event artifact.
    predictions[(predictions.model == "base") & (predictions.target_horizon_s == 5)].to_parquet(OUT / "base_5s_validation_clusters.parquet", index=False)
    base = summary[(summary.model == "base") & (summary.target_horizon_s == 5)]
    pass_rows = []
    for row in base.itertuples():
        pass_rows.append({
            "session": row.session,
            "low_gt_high_5s": row.low_markout_5s_bps > row.high_markout_5s_bps,
            "low_positive_5s": row.low_markout_5s_bps > 0,
            "low_gt_high_15s": row.low_markout_15s_bps > row.high_markout_15s_bps,
            "low_positive_15s": row.low_markout_15s_bps > 0,
            "symbol_fraction_low_gt_high_at_target": row.symbol_fraction_low_gt_high_at_target,
        })
    report = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "planned_models": len(MODELS) * len(TARGET_HORIZONS),
        "executed_models": int(summary.groupby(["model", "target_horizon_s"]).ngroups),
        "train_clusters_100ms": len(train),
        "validation_clusters_100ms": len(validation),
        "maximum_loaded_session": max(SESSIONS),
        "holdout_rows_loaded": 0,
        "base_5s_validation": pass_rows,
        "fill_simulation_performed": False,
        "decision_gate": "require_both_dates_low_gt_high_and_positive_at_5s_and_15s_plus_reasonable_symbol_breadth_before_cross_asset_test",
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
