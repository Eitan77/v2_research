from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from run0002_toxicity_signal import CACHE, HORIZONS, SESSIONS, SYMBOLS, TRAIN_SESSIONS, VALIDATION_SESSIONS, WINDOWS, auc_score, prior_values
from run0003_symmetric_cluster import clustered_data, weighted_logit_irls


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0631" / "artifacts" / "RUN-0004"
PEER_GROUPS = {
    "mega_tech": ["AAPL", "MSFT", "AMZN", "GOOGL", "META"],
    "semiconductors": ["NVDA", "AMD", "AVGO", "MU"],
    "banks": ["JPM", "BAC", "WFC"],
    "energy": ["XOM", "CVX"],
    "healthcare": ["LLY", "UNH", "JNJ"],
    "consumer": ["WMT", "COST", "HD", "DIS"],
    "high_beta_growth": ["TSLA", "UBER", "PLTR"],
}
SYMBOL_GROUP = {symbol: group for group, symbols in PEER_GROUPS.items() for symbol in symbols}
MODELS = {
    "A_book_flow": ["spread_bps", "side_size_imbalance", "quote_rate_1s", "side_trade_imbalance_1s"],
    "B_plus_local": ["spread_bps", "side_size_imbalance", "quote_rate_1s", "side_trade_imbalance_1s", "side_mid_return_1s", "side_mid_return_5s"],
    "C_plus_peer": ["spread_bps", "side_size_imbalance", "quote_rate_1s", "side_trade_imbalance_1s", "side_mid_return_1s", "side_mid_return_5s", "side_peer_residual_1s", "side_peer_residual_5s"],
}
TARGET_HORIZONS = [5, 15]


def build_peer_grid() -> pd.DataFrame:
    frames = []
    for session in SESSIONS:
        for label, start, end in WINDOWS:
            grid_start = pd.Timestamp(f"{session} {start}", tz="America/New_York").tz_convert("UTC")
            grid_end = pd.Timestamp(f"{session} {end}", tz="America/New_York").tz_convert("UTC")
            grid = pd.date_range(grid_start, grid_end, freq="100ms", inclusive="left")
            grid_ns = grid.as_unit("ns").astype("int64").to_numpy()
            returns_1 = {}
            returns_5 = {}
            for symbol in SYMBOLS:
                quotes = pd.read_parquet(CACHE / "quotes" / f"{session}_{label}_{symbol}.parquet")
                quotes.ts = pd.to_datetime(quotes.ts, utc=True)
                quotes = quotes.drop_duplicates("ts", keep="last").sort_values("ts")
                q_ns = quotes.ts.astype("int64").to_numpy()
                mid = ((quotes.bid + quotes.ask) / 2).to_numpy(float)
                current = prior_values(q_ns, mid, grid_ns)
                prior1 = prior_values(q_ns, mid, grid_ns - int(1e9))
                prior5 = prior_values(q_ns, mid, grid_ns - int(5e9))
                returns_1[symbol] = current / prior1 - 1.0
                returns_5[symbol] = current / prior5 - 1.0
            r1 = pd.DataFrame(returns_1, index=grid)
            r5 = pd.DataFrame(returns_5, index=grid)
            for symbol in SYMBOLS:
                peers = [peer for peer in PEER_GROUPS[SYMBOL_GROUP[symbol]] if peer != symbol]
                frames.append(pd.DataFrame({
                    "symbol": symbol,
                    "session": session,
                    "window": label,
                    "cluster_100ms": grid,
                    "own_return_1s": r1[symbol].to_numpy(),
                    "peer_return_1s": r1[peers].mean(axis=1).to_numpy(),
                    "own_return_5s": r5[symbol].to_numpy(),
                    "peer_return_5s": r5[peers].mean(axis=1).to_numpy(),
                }))
    return pd.concat(frames, ignore_index=True)


def add_residuals(grid: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    betas = []
    for symbol, group in grid[grid.session.isin(TRAIN_SESSIONS)].groupby("symbol"):
        row = {"symbol": symbol, "peer_group": SYMBOL_GROUP[symbol]}
        for horizon in (1, 5):
            x = group[f"peer_return_{horizon}s"].to_numpy()
            y = group[f"own_return_{horizon}s"].to_numpy()
            valid = np.isfinite(x) & np.isfinite(y)
            x = x[valid]
            y = y[valid]
            variance = np.dot(x - x.mean(), x - x.mean())
            beta = float(np.dot(x - x.mean(), y - y.mean()) / variance) if variance > 0 else 1.0
            row[f"beta_{horizon}s"] = beta
        betas.append(row)
    beta_frame = pd.DataFrame(betas)
    grid = grid.merge(beta_frame[["symbol", "beta_1s", "beta_5s"]], on="symbol", how="left", validate="many_to_one")
    for horizon in (1, 5):
        grid[f"peer_residual_{horizon}s"] = grid[f"own_return_{horizon}s"] - grid[f"beta_{horizon}s"] * grid[f"peer_return_{horizon}s"]
    return grid, beta_frame


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    candidates = clustered_data()
    peer_grid, betas = add_residuals(build_peer_grid())
    parent_rows = len(candidates)
    data = candidates.merge(
        peer_grid[["symbol", "session", "window", "cluster_100ms", "peer_residual_1s", "peer_residual_5s"]],
        on=["symbol", "session", "window", "cluster_100ms"],
        how="inner",
        validate="many_to_one",
    )
    data["side_peer_residual_1s"] = data.side * data.peer_residual_1s
    data["side_peer_residual_5s"] = data.side * data.peer_residual_5s
    required = sorted(set(sum(MODELS.values(), [])))
    data = data.replace([np.inf, -np.inf], np.nan).dropna(subset=required)
    train = data[data.session.isin(TRAIN_SESSIONS)].copy()
    validation = data[data.session.isin(VALIDATION_SESSIONS)].copy()
    counts = train.groupby(["symbol", "session"]).size().rename("group_n")
    train = train.join(counts, on=["symbol", "session"])
    train["weight"] = 1.0 / train.group_n
    train["weight"] *= len(train) / train.weight.sum()
    summary_rows = []
    coefficient_rows = []
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
            scored = validation[["symbol", "session"] + [f"markout_{h:g}s_bps" for h in HORIZONS]].copy()
            scored["bucket"] = np.where(probability <= low_cut, "low", np.where(probability >= high_cut, "high", "middle"))
            for feature, value in zip(["intercept"] + features, beta):
                coefficient_rows.append({"model": model_name, "target_horizon_s": target_horizon, "feature": feature, "coefficient": value})
            for session, group in scored.groupby("session"):
                mask = validation.session.eq(session).to_numpy()
                symbol_bucket = group.groupby(["symbol", "bucket"])[[f"markout_{h:g}s_bps" for h in HORIZONS]].mean().reset_index()
                means = symbol_bucket.groupby("bucket")[[f"markout_{h:g}s_bps" for h in HORIZONS]].mean()
                low = means.loc["low"]
                high = means.loc["high"]
                per_symbol = symbol_bucket.pivot(index="symbol", columns="bucket", values=target).dropna()
                summary_rows.append({
                    "model": model_name,
                    "target_horizon_s": target_horizon,
                    "session": session,
                    "auc": auc_score(yv[mask], probability[mask]),
                    "symbols_with_both_buckets": len(per_symbol),
                    "symbol_fraction_low_gt_high_at_target": float((per_symbol.low > per_symbol.high).mean()) if len(per_symbol) else np.nan,
                    **{f"low_markout_{h:g}s_bps": float(low[f"markout_{h:g}s_bps"]) for h in HORIZONS},
                    **{f"high_markout_{h:g}s_bps": float(high[f"markout_{h:g}s_bps"]) for h in HORIZONS},
                })
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(OUT / "abc_validation_summary.csv", index=False)
    pd.DataFrame(coefficient_rows).to_csv(OUT / "model_coefficients.csv", index=False)
    betas.to_csv(OUT / "train_only_peer_betas.csv", index=False)
    primary = summary[summary.target_horizon_s == 5]
    comparison = primary.pivot(index="session", columns="model", values=["auc", "low_markout_5s_bps", "high_markout_5s_bps", "symbol_fraction_low_gt_high_at_target"])
    report = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "planned_models": 6,
        "executed_models": int(summary.groupby(["model", "target_horizon_s"]).ngroups),
        "parent_candidate_clusters": parent_rows,
        "clusters_after_peer_join": len(data),
        "cluster_attrition": parent_rows - len(data),
        "train_clusters": len(train),
        "validation_clusters": len(validation),
        "maximum_loaded_session": max(SESSIONS),
        "holdout_rows_loaded": 0,
        "primary_5s_comparison": json.loads(primary.to_json(orient="records")),
        "fill_simulation_performed": False,
        "decision_gate": "cross_asset_incremental_only_if_C_preserves_both_dates_and_improves_controls_without_concentration",
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(comparison.to_string())
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
