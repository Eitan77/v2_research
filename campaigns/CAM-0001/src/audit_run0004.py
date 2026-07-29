from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from cam0001 import RunConfig, simulate, simulate_invalidation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    frame = pd.read_parquet(args.cache)
    specs = []
    for sma in [20, 50]:
        for cost in [5.0, 20.0]:
            specs.append((f"sma{sma}_fixed_cost{int(cost)}", "fixed", RunConfig(
                lookback=5, market_sma=sma, holding_sessions=10, breadth=2,
                cost_bps_per_side=cost,
            )))
            for rule in ["market", "fund_momentum", "either"]:
                specs.append((f"sma{sma}_{rule}_cost{int(cost)}", rule, RunConfig(
                    lookback=5, market_sma=sma, holding_sessions=10, breadth=2,
                    cost_bps_per_side=cost,
                )))
    if len(specs) != 16:
        raise RuntimeError("unexpected RUN-0004 variant count")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, rule, config in specs:
        if rule == "fixed":
            daily, trades, metrics = simulate(frame, config)
            exit_counts = {"max_holding": int(len(trades))}
        else:
            daily, trades, metrics = simulate_invalidation(frame, config, rule)
            exit_counts = metrics["exit_reason_counts"]
        row = {"variant": name, "exit_rule": rule, **asdict(config)}
        row.update({
            "full_net": metrics["net_full_period_simple_return"],
            "full_max_dd": metrics["standard_max_drawdown"],
            "full_recovery_days": metrics["max_full_recovery_time_days"],
            "full_utilization": metrics["average_capital_utilization"],
            "full_turnover": metrics["gross_notional_turnover"],
            "full_beta": metrics["market_beta_on_active_days"],
            "exit_reason_counts": json.dumps(exit_counts, sort_keys=True),
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
        if cost == 5.0:
            detail = args.output_dir / name
            detail.mkdir(exist_ok=True)
            daily.to_csv(detail / "daily.csv", index=False)
            trades.to_csv(detail / "trades.csv", index=False)
            (detail / "metrics.json").write_text(
                json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
            )
    pd.DataFrame(rows).to_csv(args.output_dir / "exit_family.csv", index=False)
    contract = {
        "executed_variant_count": len(rows),
        "expected_variant_count": 16,
        "qqq_sma": [20, 50],
        "exit_rules": ["fixed", "market", "fund_momentum", "either"],
        "cost_bps_per_side": [5, 20],
        "maximum_holding_sessions": 10,
        "invalidation_timing": "Evaluate completed close; exit next open.",
        "reentry_policy": (
            "Preserve the parent 10-session decision block after early exit to "
            "isolate exit behavior; freed capital remains cash until next block."
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
