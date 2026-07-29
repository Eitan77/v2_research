from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from cam0001 import RunConfig, simulate, simulate_price_stop


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
        config = RunConfig(
            trade_symbols=("TQQQ", "SOXL"), lookback=5, market_sma=sma,
            holding_sessions=10, breadth=2, require_sma_rising=True,
            cost_bps_per_side=5.0,
        )
        _, _, base_metrics = simulate(frame, config)
        rows.append(flatten(f"sma{sma}_no_stop", base_metrics, config, None, None))
        details[f"sma{sma}_no_stop"] = base_metrics
        for stop in [0.10, 0.15, 0.20]:
            for slippage in [10.0, 100.0]:
                _, trades, metrics = simulate_price_stop(
                    frame, config, stop, stop_slippage_bps=slippage
                )
                name = f"sma{sma}_stop{int(stop*100)}_slip{int(slippage)}"
                rows.append(flatten(name, metrics, config, stop, slippage))
                details[name] = metrics
    if len(rows) != 14:
        raise RuntimeError("RUN-0009 executed variant count mismatch")
    pd.DataFrame(rows).to_csv(args.output_dir / "stop_family.csv", index=False)
    (args.output_dir / "metrics.json").write_text(
        json.dumps(details, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "executed_variant_count": len(rows),
        "expected_variant_count": 14,
        "stops": [0.10, 0.15, 0.20],
        "stop_slippage_bps": [10, 100],
        "fill_rule": (
            "If bar open is at/below stop, fill at open. Otherwise if daily low "
            "touches stop, fill at stop less adverse slippage. Otherwise hold."
        ),
        "decision_schedule": "Original 10-session blocks preserved after a stop.",
        "loaded_max_date": str(pd.to_datetime(frame["date"]).max().date()),
        "holdout_rows_loaded": int((pd.to_datetime(frame["date"]) >= "2026-05-01").sum()),
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(contract, indent=2))


def flatten(name, metrics, config, stop, slippage):
    row = {"variant": name, **asdict(config), "stop": stop, "stop_slippage_bps": slippage}
    row.update({
        "full_net": metrics["net_full_period_simple_return"],
        "full_max_dd": metrics["standard_max_drawdown"],
        "full_recovery_days": metrics["max_full_recovery_time_days"],
        "full_utilization": metrics["average_capital_utilization"],
        "full_turnover": metrics["gross_notional_turnover"],
        "full_beta": metrics["market_beta_on_active_days"],
        "exit_reason_counts": json.dumps(metrics.get("exit_reason_counts", {}), sort_keys=True),
    })
    for label in ["18m", "15m", "12m"]:
        w = metrics["recent_windows"][label]
        row.update({
            f"{label}_avg_month": w["average_monthly_net_simple_return"],
            f"{label}_median_month": w["median_monthly_net_simple_return"],
            f"{label}_negative_months": w["negative_month_count"],
            f"{label}_max_dd": w["standard_max_drawdown"],
            f"{label}_recovery_days": w["max_full_recovery_time_days"],
        })
    return row


if __name__ == "__main__":
    main()
