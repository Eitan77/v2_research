from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from cam0001 import RunConfig, simulate


VARIANTS = {
    "candidate_5bps_delay1": {"cost_bps_per_side": 5.0, "entry_delay_sessions": 1},
    "candidate_10bps_delay1": {"cost_bps_per_side": 10.0, "entry_delay_sessions": 1},
    "candidate_20bps_delay1": {"cost_bps_per_side": 20.0, "entry_delay_sessions": 1},
    "candidate_5bps_delay2": {"cost_bps_per_side": 5.0, "entry_delay_sessions": 2},
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    frame = pd.read_parquet(args.cache)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    details: dict[str, dict] = {}
    for name, changed in VARIANTS.items():
        config = RunConfig(
            lookback=5,
            market_sma=20,
            holding_sessions=10,
            breadth=2,
            require_sma_rising=True,
            **changed,
        )
        _, _, metrics = simulate(frame, config)
        row = {"variant": name, **asdict(config)}
        row.update(
            {
                "full_net": metrics["net_full_period_simple_return"],
                "full_max_dd": metrics["standard_max_drawdown"],
                "full_recovery_days": metrics["max_full_recovery_time_days"],
                "full_decisions": metrics["independent_entry_decisions"],
            }
        )
        for label in ["18m", "15m", "12m"]:
            window = metrics["recent_windows"][label]
            row.update(
                {
                    f"{label}_net": window["net_simple_return"],
                    f"{label}_avg_month": window["average_monthly_net_simple_return"],
                    f"{label}_median_month": window["median_monthly_net_simple_return"],
                    f"{label}_negative_months": window["negative_month_count"],
                    f"{label}_max_dd": window["standard_max_drawdown"],
                    f"{label}_recovery_days": window["max_full_recovery_time_days"],
                    f"{label}_decisions": window["independent_entry_decisions"],
                }
            )
        rows.append(row)
        details[name] = metrics
    if len(rows) != 4:
        raise RuntimeError("RUN-0011 executed variant count mismatch")
    pd.DataFrame(rows).to_csv(args.output_dir / "final_family.csv", index=False)
    (args.output_dir / "metrics.json").write_text(
        json.dumps(details, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "executed_variant_count": len(rows),
        "expected_variant_count": 4,
        "variants": VARIANTS,
        "loaded_max_date": str(pd.to_datetime(frame["date"]).max().date()),
        "holdout_rows_loaded": int((pd.to_datetime(frame["date"]) >= "2026-05-01").sum()),
        "sample_policy": (
            "Costs do not change eligibility. The two-session entry variant uses "
            "the same causal decisions but necessarily drops any terminal decision "
            "that cannot complete its full ten-session hold before the cutoff."
        ),
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(contract, indent=2))


if __name__ == "__main__":
    main()
