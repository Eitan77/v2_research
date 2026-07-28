"""Deterministic pre-cutoff feature-correlation and market-structure audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


FEATURES = [
    "close_vs_sma_5", "close_vs_ema_5", "close_vs_sma_10", "close_vs_ema_10",
    "close_vs_sma_20", "close_vs_ema_20", "close_vs_sma_50", "close_vs_ema_50",
    "close_vs_sma_100", "close_vs_ema_100", "close_vs_sma_200", "close_vs_ema_200",
    "bb_percent_b_20_2", "bb_bandwidth_20_2", "atr_pct_14", "rsi_14",
    "macd_hist_12_26_9", "roc_10", "momentum_10", "stoch_k_14", "williams_r_14",
    "cci_20", "plus_di_14", "minus_di_14", "adx_14", "cmf_20", "mfi_14",
    "hl_range_pct", "body_pct", "upper_wick_pct", "lower_wick_pct", "relative_volume_20",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="D:/AlgoResearch/data/catalog.duckdb", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--sample-rows", type=int, default=500_000)
    ap.add_argument("--threads", type=int, default=16)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(args.catalog), read_only=True)
    con.execute(f"set threads={max(1, args.threads)}")
    cols = ", ".join(["symbol", "timestamp", "date"] + FEATURES + ["fwd_return_1", "fwd_return_4", "fwd_return_12", "fwd_return_24"])
    query = f"""
        select *
        from (
            select {cols}
            from research_matrix
            where timeframe = ? and timestamp < '2026-06-01'
        ) as pre_cutoff_15m
        using sample reservoir({int(args.sample_rows)} rows) repeatable (20260709)
    """
    try:
        frame = con.execute(query, [args.timeframe]).fetchdf()
    finally:
        con.close()
    numeric = [c for c in FEATURES + ["fwd_return_1", "fwd_return_4", "fwd_return_12", "fwd_return_24"] if c in frame.columns]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    usable = frame[numeric].replace([np.inf, -np.inf], np.nan)
    corr = usable.corr(method="spearman", min_periods=1000)
    feature_corr = corr.loc[[c for c in FEATURES if c in corr.index], [c for c in FEATURES if c in corr.columns]]
    pairs = []
    for i, left in enumerate(feature_corr.columns):
        for right in feature_corr.columns[i + 1:]:
            value = feature_corr.loc[left, right]
            if pd.notna(value):
                pairs.append({"left": left, "right": right, "spearman_corr": float(value), "abs_corr": abs(float(value))})
    pd.DataFrame(pairs).sort_values("abs_corr", ascending=False).to_csv(args.out / "feature_correlation_pairs.csv", index=False)
    summary = pd.DataFrame({
        "feature": FEATURES,
        "non_null": [usable[c].notna().sum() if c in usable.columns else 0 for c in FEATURES],
        "mean": [usable[c].mean() if c in usable.columns else np.nan for c in FEATURES],
        "std": [usable[c].std() if c in usable.columns else np.nan for c in FEATURES],
        "abs_corr_to_fwd_1": [abs(corr.loc[c, "fwd_return_1"]) if c in corr.index and "fwd_return_1" in corr.columns else np.nan for c in FEATURES],
        "abs_corr_to_fwd_4": [abs(corr.loc[c, "fwd_return_4"]) if c in corr.index and "fwd_return_4" in corr.columns else np.nan for c in FEATURES],
    }).sort_values("abs_corr_to_fwd_4", ascending=False)
    summary.to_csv(args.out / "feature_predictive_summary.csv", index=False)
    frame["ts"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["ny_minute"] = frame["ts"].dt.tz_convert("America/New_York").dt.hour * 60 + frame["ts"].dt.tz_convert("America/New_York").dt.minute
    structure = frame.groupby("ny_minute", dropna=True)[["fwd_return_1", "fwd_return_4", "fwd_return_12", "relative_volume_20", "atr_pct_14"]].agg(["count", "mean", "median"]).reset_index()
    structure.to_csv(args.out / "market_structure_by_time.csv", index=False)
    meta = {"timeframe": args.timeframe, "sample_rows_requested": args.sample_rows, "rows_returned": int(len(frame)), "threads": args.threads, "cutoff_exclusive": "2026-06-01", "method": "Spearman feature correlations plus RTH minute-of-day forward-return structure"}
    (args.out / "audit_metadata.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
