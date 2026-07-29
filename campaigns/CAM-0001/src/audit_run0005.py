from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from cam0001 import RunConfig, simulate


FILTERS = {
    "baseline": {},
    "all_funds_positive": {"require_all_funds_positive": True},
    "qqq_positive": {"require_qqq_positive_lookback": True},
    "participation_and_qqq": {
        "require_all_funds_positive": True,
        "require_qqq_positive_lookback": True,
    },
    "sma_rising": {"require_sma_rising": True},
    "orderly_volatility": {"require_orderly_volatility": True},
    "trend_efficiency": {"require_trend_efficiency": True},
    "participation_and_efficiency": {
        "require_all_funds_positive": True,
        "require_trend_efficiency": True,
    },
    "qqq_and_orderly": {
        "require_qqq_positive_lookback": True,
        "require_orderly_volatility": True,
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    frame = pd.read_parquet(args.cache)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    details = {}
    for sma in [20, 50]:
        baseline_decisions = None
        for name, kwargs in FILTERS.items():
            config = RunConfig(
                lookback=5, market_sma=sma, holding_sessions=10, breadth=2,
                cost_bps_per_side=5.0, **kwargs,
            )
            daily, trades, metrics = simulate(frame, config)
            if name == "baseline":
                baseline_decisions = metrics["independent_entry_decisions"]
            row = {"variant": f"sma{sma}_{name}", "filter_name": name, **asdict(config)}
            row.update({
                "full_net": metrics["net_full_period_simple_return"],
                "full_max_dd": metrics["standard_max_drawdown"],
                "full_recovery_days": metrics["max_full_recovery_time_days"],
                "full_decisions": metrics["independent_entry_decisions"],
                "full_decision_attrition_vs_parent": (
                    baseline_decisions - metrics["independent_entry_decisions"]
                    if baseline_decisions is not None else 0
                ),
                "full_utilization": metrics["average_capital_utilization"],
                "full_beta": metrics["market_beta_on_active_days"],
            })
            for label in ["18m", "15m", "12m"]:
                window = metrics["recent_windows"][label]
                row.update({
                    f"{label}_net": window["net_simple_return"],
                    f"{label}_avg_month": window["average_monthly_net_simple_return"],
                    f"{label}_median_month": window["median_monthly_net_simple_return"],
                    f"{label}_negative_months": window["negative_month_count"],
                    f"{label}_max_dd": window["standard_max_drawdown"],
                    f"{label}_recovery_days": window["max_full_recovery_time_days"],
                    f"{label}_decisions": window["independent_entry_decisions"],
                })
            rows.append(row)
            details[f"sma{sma}_{name}"] = metrics
    if len(rows) != 18:
        raise RuntimeError("RUN-0005 executed variant count mismatch")
    pd.DataFrame(rows).to_csv(args.output_dir / "activation_family.csv", index=False)
    (args.output_dir / "metrics.json").write_text(
        json.dumps(details, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "executed_variant_count": len(rows),
        "expected_variant_count": 18,
        "filters": FILTERS,
        "threshold_policy": (
            "No tuned numeric cutoff. Volatility and efficiency compare the "
            "current completed-bar statistic with its prior rolling 252-session "
            "median (minimum 126 prior observations)."
        ),
        "sample_policy": (
            "No dates, symbols, or rows are removed. Report loss of independent "
            "entry decisions versus the same-SMA baseline as eligibility attrition."
        ),
        "loaded_max_date": str(pd.to_datetime(frame["date"]).max().date()),
        "holdout_rows_loaded": int((pd.to_datetime(frame["date"]) >= "2026-05-01").sum()),
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(contract, indent=2))


if __name__ == "__main__":
    main()
