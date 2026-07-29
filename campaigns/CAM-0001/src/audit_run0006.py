from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from cam0001 import RunConfig, simulate


SCHEMES = [
    ("equal", "equal", "none"),
    ("inverse_vol", "inverse_vol", "none"),
    ("soxl_cap50", "soxl_cap50", "none"),
    ("equal_scaled", "equal", "qqq_median_ratio"),
    ("inverse_vol_scaled", "inverse_vol", "qqq_median_ratio"),
    ("soxl_cap50_scaled", "soxl_cap50", "qqq_median_ratio"),
]


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
        for name, weight_scheme, scaling in SCHEMES:
            config = RunConfig(
                lookback=5,
                market_sma=sma,
                holding_sessions=10,
                breadth=2,
                cost_bps_per_side=5.0,
                require_sma_rising=True,
                weight_scheme=weight_scheme,
                volatility_scaling=scaling,
            )
            daily, trades, metrics = simulate(frame, config)
            row = {"variant": f"sma{sma}_{name}", **asdict(config)}
            row.update({
                "full_net": metrics["net_full_period_simple_return"],
                "full_max_dd": metrics["standard_max_drawdown"],
                "full_recovery_days": metrics["max_full_recovery_time_days"],
                "full_decisions": metrics["independent_entry_decisions"],
                "full_utilization": metrics["average_capital_utilization"],
                "full_max_exposure": metrics["maximum_gross_exposure"],
                "full_turnover": metrics["gross_notional_turnover"],
                "full_beta": metrics["market_beta_on_active_days"],
                "full_top_symbol_share": metrics["top_symbol_net_contribution_share"],
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
                    f"{label}_symbol_net": json.dumps(window["symbol_net_contribution"], sort_keys=True),
                })
            rows.append(row)
            details[f"sma{sma}_{name}"] = metrics
    if len(rows) != 12:
        raise RuntimeError("RUN-0006 executed variant count mismatch")
    pd.DataFrame(rows).to_csv(args.output_dir / "sizing_family.csv", index=False)
    (args.output_dir / "metrics.json").write_text(
        json.dumps(details, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "executed_variant_count": len(rows),
        "expected_variant_count": 12,
        "schemes": SCHEMES,
        "volatility_scaling": (
            "Exposure = min(1, prior rolling 252-session median QQQ 20-session "
            "volatility / current completed-bar QQQ 20-session volatility); "
            "unavailable early reference leaves exposure at 1."
        ),
        "soxl_cap": (
            "SOXL weight capped at 0.5; unused weight remains cash when SOXL "
            "is the sole eligible fund."
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
