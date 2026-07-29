from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from cam0001 import RunConfig, simulate


LOOKBACKS = [5, 20, 60]
MARKET_SMAS = [20, 50, 100, 200]
HOLDING_SESSIONS = [1, 3, 5, 10]
BREADTHS = [1, 2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    frame = pd.read_parquet(args.cache)
    rows = []
    expected = len(LOOKBACKS) * len(MARKET_SMAS) * len(HOLDING_SESSIONS) * len(BREADTHS)
    for lookback, market_sma, holding, breadth in itertools.product(
        LOOKBACKS, MARKET_SMAS, HOLDING_SESSIONS, BREADTHS
    ):
        config = RunConfig(
            lookback=lookback,
            market_sma=market_sma,
            holding_sessions=holding,
            breadth=breadth,
            cost_bps_per_side=5.0,
        )
        _, _, metrics = simulate(frame, config)
        row = asdict(config)
        row.update(
            {
                "full_net": metrics["net_full_period_simple_return"],
                "full_max_dd": metrics["standard_max_drawdown"],
                "full_decisions": metrics["independent_entry_decisions"],
                "full_beta": metrics["market_beta_on_active_days"],
                "top_symbol_share": metrics["top_symbol_net_contribution_share"],
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
                    f"{label}_soxl_net": window["symbol_net_contribution"].get("SOXL", 0.0),
                    f"{label}_tqqq_net": window["symbol_net_contribution"].get("TQQQ", 0.0),
                }
            )
        rows.append(row)
    if len(rows) != expected:
        raise RuntimeError(f"executed {len(rows)} variants, expected {expected}")
    result = pd.DataFrame(rows).sort_values(
        ["12m_negative_months", "12m_max_dd", "12m_avg_month"],
        ascending=[True, True, False],
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output_dir / "family.csv", index=False)
    contract = {
        "executed_variant_count": len(result),
        "lookbacks": LOOKBACKS,
        "market_smas": MARKET_SMAS,
        "holding_sessions": HOLDING_SESSIONS,
        "breadths": BREADTHS,
        "cost_bps_per_side": 5.0,
        "loaded_max_date": str(frame["date"].max().date()),
        "holdout_rows_loaded": int((pd.to_datetime(frame["date"]) >= "2026-05-01").sum()),
        "selection_policy": (
            "No composite score and no automatic winner. Diagnose the complete "
            "12/15/18-month path, drawdown, both-fund contribution, full-history "
            "failure regimes, and parameter-neighbor behavior."
        ),
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(contract, indent=2))


if __name__ == "__main__":
    main()
