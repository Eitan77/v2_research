from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from run0010 import attach_high, summarize, trade_from_path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--paths", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    temp = a.output_dir / "audit_duckdb_tmp"
    temp.mkdir(exist_ok=True)
    paths = pd.read_parquet(a.paths)
    paths["date"] = pd.to_datetime(paths["date"])
    paths = paths[paths["anchor"] == "f15_a6_s10"].copy()
    paths, high_missing = attach_high(paths, temp)
    groups = []
    for _, group in paths.groupby(["symbol", "date", "event_ts"], sort=False):
        by = group.set_index("offset_min")
        if 62 in by.index and float(by.loc[1, "completed_close"]) > float(by.loc[0, "completed_close"]):
            groups.append(group)
    trades = pd.DataFrame([trade_from_path(g, None, 0.04) for g in groups])
    metrics, selected = summarize(trades)
    selected.to_csv(a.output_dir / "leader_events.csv", index=False)
    symbols = selected.groupby("symbol").agg(
        events=("weighted_net", "size"),
        net_contribution=("weighted_net", "sum"),
        mean_net=("net_return", "mean"),
    ).sort_values("net_contribution", ascending=False)
    symbols.to_csv(a.output_dir / "leader_symbols.csv")
    months = pd.period_range("2024-11", "2026-04", freq="M")
    monthly = selected.assign(
        month=pd.to_datetime(selected["date"]).dt.to_period("M")
    ).groupby("month")["weighted_net"].sum().reindex(months, fill_value=0.0)
    monthly.rename("net_pnl").to_csv(a.output_dir / "leader_monthly.csv")
    positive = selected.loc[selected["weighted_net"] > 0, "weighted_net"].sum()
    loo = {}
    for symbol in selected["symbol"].unique():
        m, _ = summarize(trades[trades["symbol"] != symbol])
        loo[symbol] = m["net"]
    audit = {
        "configuration": "f15 residual6 surprise10 stocks reclaim2 hold60 target4 no_stop cost10",
        "metrics": metrics, "raw_high_missing_rows_with_fallback": high_missing,
        "unique_symbols": int(selected["symbol"].nunique()),
        "top1_positive_contribution_fraction": float(
            selected["weighted_net"].max()/positive
        ),
        "top5_positive_contribution_fraction": float(
            selected.nlargest(5, "weighted_net")["weighted_net"].sum()/positive
        ),
        "leave_one_symbol_out_min_net": float(min(loo.values())),
        "leave_one_symbol_out_max_net": float(max(loo.values())),
        "top_symbols": symbols.head(10).reset_index().to_dict(orient="records"),
        "monthly": {str(k): float(v) for k, v in monthly.items()},
    }
    (a.output_dir / "leader_audit.json").write_text(json.dumps(audit, indent=2) + "\n")


if __name__ == "__main__":
    main()
