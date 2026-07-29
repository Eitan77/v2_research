from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from cam0001 import RunConfig, simulate


def concentration(trades: pd.DataFrame, start: str) -> dict:
    selected = trades[trades["entry_date"] >= pd.Timestamp(start)].copy()
    values = selected["net_return_contribution"].sort_values(ascending=False)
    net = float(values.sum())
    positives = values[values > 0]
    negatives = values[values < 0]
    return {
        "leg_count": int(len(selected)),
        "decision_count": int(selected["entry_date"].nunique()),
        "net": net,
        "top_1_positive_share_of_net": float(positives.head(1).sum() / net) if net > 0 else None,
        "top_5_positive_share_of_net": float(positives.head(5).sum() / net) if net > 0 else None,
        "worst_1_loss": float(negatives.min()) if len(negatives) else 0.0,
        "worst_5_loss": float(negatives.head(5).sum()) if len(negatives) else 0.0,
        "positive_leg_fraction": float((selected["net_return_contribution"] > 0).mean())
        if len(selected)
        else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    frame = pd.read_parquet(args.cache)
    specs = []
    for sma in [20, 50]:
        for cost in [5.0, 10.0, 20.0]:
            specs.append((f"sma{sma}_cost{int(cost)}", RunConfig(
                lookback=5, market_sma=sma, holding_sessions=10, breadth=2,
                cost_bps_per_side=cost,
            )))
        specs.append((f"sma{sma}_delay2", RunConfig(
            lookback=5, market_sma=sma, holding_sessions=10, breadth=2,
            entry_delay_sessions=2, cost_bps_per_side=5.0,
        )))
        for symbols in [("TQQQ",), ("SOXL",)]:
            specs.append((f"sma{sma}_{symbols[0].lower()}_only", RunConfig(
                trade_symbols=symbols, lookback=5, market_sma=sma,
                holding_sessions=10, breadth=1, cost_bps_per_side=5.0,
            )))
        for lookback in [3, 10]:
            specs.append((f"sma{sma}_lookback{lookback}", RunConfig(
                lookback=lookback, market_sma=sma, holding_sessions=10,
                breadth=2, cost_bps_per_side=5.0,
            )))
        specs.append((f"sma{sma}_hold7", RunConfig(
            lookback=5, market_sma=sma, holding_sessions=7, breadth=2,
            cost_bps_per_side=5.0,
        )))

    if len(specs) != 18 or len({name for name, _ in specs}) != 18:
        raise RuntimeError("RUN-0003 spec count or identifiers invalid")
    rows = []
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, config in specs:
        daily, trades, metrics = simulate(frame, config)
        row = {"variant": name, **asdict(config)}
        row.update({
            "full_net": metrics["net_full_period_simple_return"],
            "full_max_dd": metrics["standard_max_drawdown"],
            "full_recovery_days": metrics["max_full_recovery_time_days"],
            "full_beta": metrics["market_beta_on_active_days"],
            "full_decisions": metrics["independent_entry_decisions"],
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
        row.update({f"12m_{k}": v for k, v in concentration(trades, "2025-05-01").items()})
        rows.append(row)
        if name in {"sma20_cost5", "sma50_cost5"}:
            core = args.output_dir / name
            core.mkdir(exist_ok=True)
            daily.to_csv(core / "daily.csv", index=False)
            trades.to_csv(core / "trades.csv", index=False)
            (core / "metrics.json").write_text(
                json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
            )
    pd.DataFrame(rows).to_csv(args.output_dir / "audit_family.csv", index=False)
    contract = {
        "executed_variant_count": len(rows),
        "expected_variant_count": 18,
        "loaded_max_date": str(pd.to_datetime(frame["date"]).max().date()),
        "holdout_rows_loaded": int((pd.to_datetime(frame["date"]) >= "2026-05-01").sum()),
        "dimensions": {
            "qqq_sma": [20, 50],
            "cost_bps_per_side": [5, 10, 20],
            "entry_delay_sessions": [1, 2],
            "component_universes": [["TQQQ", "SOXL"], ["TQQQ"], ["SOXL"]],
            "lookback_neighbors": [3, 5, 10],
            "holding_neighbors": [7, 10],
        },
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(contract, indent=2))


if __name__ == "__main__":
    main()
