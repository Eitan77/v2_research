from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def stats(frame: pd.DataFrame, ret: str) -> dict:
    x = frame[ret].dropna()
    return {
        "events": int(len(x)),
        "gross_sum": float(x.sum()),
        "gross_mean": float(x.mean()) if len(x) else None,
        "median": float(x.median()) if len(x) else None,
        "positive_fraction": float((x > 0).mean()) if len(x) else None,
        "net_mean_10bps": float(x.mean() - 0.002) if len(x) else None,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--events", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    e = pd.read_parquet(a.events)
    e["event_ts"] = pd.to_datetime(e["event_ts"], utc=True)
    e["gross30"] = e["exit30_price"] / e["entry_price"] - 1
    e["gross60"] = e["exit60_price"] / e["entry_price"] - 1
    e["shock_abs"] = -e["ret60"]
    e["surprise"] = e["shock_abs"] / e["prior60_normal"]
    e["shock_bin"] = pd.cut(
        e["shock_abs"], [0.04, 0.05, 0.06, 0.08, np.inf],
        labels=["4-5%", "5-6%", "6-8%", ">=8%"], include_lowest=True,
    )
    e["surprise_bin"] = pd.cut(
        e["surprise"], [8, 10, np.inf], labels=["8-10x", ">=10x"],
        include_lowest=True,
    )
    e["time_bin"] = pd.cut(
        e["minute_index"], [59, 119, 239, np.inf],
        labels=["first_2h", "middle", "late"], include_lowest=True,
    )
    cluster = e.groupby("event_ts")["symbol"].transform("count")
    e["cluster_bin"] = pd.cut(
        cluster, [0, 1, 3, 10, np.inf],
        labels=["solo", "2-3", "4-10", ">10"], include_lowest=True,
    )
    rows = []
    for dimension in ["shock_bin", "surprise_bin", "time_bin", "cluster_bin"]:
        for value, group in e.groupby(dimension, observed=True):
            for horizon in ["gross30", "gross60"]:
                rows.append({"dimension": dimension, "value": str(value),
                             "horizon": horizon, **stats(group, horizon)})
    pd.DataFrame(rows).to_csv(a.output_dir / "groups.csv", index=False)
    symbols = []
    for symbol, group in e.groupby("symbol"):
        symbols.append({"symbol": symbol, **{f"60_{k}": v for k, v in stats(group, "gross60").items()}})
    pd.DataFrame(symbols).sort_values("60_gross_sum", ascending=False).to_csv(
        a.output_dir / "symbols.csv", index=False
    )
    ranked = e.sort_values("gross60", ascending=False)
    positive_total = float(ranked.loc[ranked["gross60"] > 0, "gross60"].sum())
    diag = {
        "all_30": stats(e, "gross30"), "all_60": stats(e, "gross60"),
        "event_count": int(len(e)), "symbols": int(e["symbol"].nunique()),
        "same_minute_clusters": int(e["event_ts"].nunique()),
        "largest_same_minute_cluster": int(cluster.max()),
        "top_1_positive_share": (
            float(ranked.iloc[0]["gross60"] / positive_total) if positive_total else None
        ),
        "top_10_positive_share": (
            float(ranked.head(10)["gross60"].clip(lower=0).sum() / positive_total)
            if positive_total else None
        ),
        "loaded_max_date": str(pd.to_datetime(e["date"]).max().date()),
        "holdout_rows_loaded": int((pd.to_datetime(e["date"]) >= "2026-05-01").sum()),
    }
    (a.output_dir / "diagnostics.json").write_text(json.dumps(diag, indent=2) + "\n")
    contract = {"executed_strategy_variant_count": 0, "expected_strategy_variant_count": 0,
                "diagnostic_dimensions": 4, "holdout_rows_loaded": diag["holdout_rows_loaded"]}
    (a.output_dir / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(json.dumps(diag, indent=2))


if __name__ == "__main__":
    main()
