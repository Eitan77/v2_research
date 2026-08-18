from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pandas as pd

from run0002_toxicity_signal import TRAIN_SESSIONS
from run0003_symmetric_cluster import clustered_data, weighted_logit_irls
from run0004_cross_asset import MODELS as RUN4_MODELS, add_residuals, build_peer_grid


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0631" / "artifacts" / "RUN-0005"
FEATURES = ["spread_bps", "side_size_imbalance", "quote_rate_1s", "side_trade_imbalance_1s"]


def main() -> None:
    candidates = clustered_data()
    peer_grid, _ = add_residuals(build_peer_grid())
    data = candidates.merge(
        peer_grid[["symbol", "session", "window", "cluster_100ms", "peer_residual_1s", "peer_residual_5s"]],
        on=["symbol", "session", "window", "cluster_100ms"],
        how="inner",
        validate="many_to_one",
    )
    data["side_peer_residual_1s"] = data.side * data.peer_residual_1s
    data["side_peer_residual_5s"] = data.side * data.peer_residual_5s
    required = sorted(set(sum(RUN4_MODELS.values(), [])))
    data = data.replace([np.inf, -np.inf], np.nan).dropna(subset=required)
    train = data[data.session.isin(TRAIN_SESSIONS)].copy()
    counts = train.groupby(["symbol", "session"]).size().rename("group_n")
    train = train.join(counts, on=["symbol", "session"])
    train["weight"] = 1.0 / train.group_n
    train["weight"] *= len(train) / train.weight.sum()
    mean = np.average(train[FEATURES], axis=0, weights=train.weight)
    variance = np.average((train[FEATURES].to_numpy() - mean) ** 2, axis=0, weights=train.weight)
    std = np.sqrt(np.maximum(variance, 1e-12))
    x = np.c_[np.ones(len(train)), (train[FEATURES].to_numpy() - mean) / std]
    y = (train.markout_5s_bps.to_numpy() < 0).astype(float)
    beta = weighted_logit_irls(x, y, train.weight.to_numpy())
    saved = pd.read_csv(ROOT / "campaigns" / "CAM-0631" / "artifacts" / "RUN-0004" / "model_coefficients.csv")
    expected = saved[(saved.model == "A_book_flow") & (saved.target_horizon_s == 5)].set_index("feature").loc[["intercept"] + FEATURES].coefficient.to_numpy()
    maximum_difference = float(np.max(np.abs(beta - expected)))
    if maximum_difference > 1e-10:
        raise RuntimeError(f"RUN-0004 coefficient reproduction failed: {maximum_difference}")
    probability = 1.0 / (1.0 + np.exp(-np.clip(x @ beta, -30, 30)))
    low_cut, high_cut = np.quantile(probability, [0.2, 0.8])
    package = {
        "model": "RUN-0004_A_book_flow_target_5s",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "features": FEATURES,
        "mean": mean.tolist(),
        "std": std.tolist(),
        "beta_intercept_then_features": beta.tolist(),
        "training_low_cut": float(low_cut),
        "training_high_cut": float(high_cut),
        "training_clusters": len(train),
        "coefficient_reproduction_max_abs_difference": maximum_difference,
        "confirmation_data_accessed": False,
    }
    canonical = json.dumps(package, sort_keys=True, separators=(",", ":"))
    package["package_sha256"] = sha256(canonical.encode()).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "frozen_model.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(package, indent=2))


if __name__ == "__main__":
    main()
