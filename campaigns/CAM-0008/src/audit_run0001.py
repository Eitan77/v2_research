from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


TOLERANCE = 1e-10
CUTOFF = pd.Timestamp("2026-04-30")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def maximum_concurrent_exposure(trades: pd.DataFrame) -> tuple[float, float]:
    allocated = trades.loc[
        trades["position_fraction"].gt(0),
        [
            "variant_id",
            "symbol",
            "entry_timestamp",
            "exit_timestamp",
            "position_fraction",
        ],
    ].copy()
    entries = allocated.rename(columns={"entry_timestamp": "timestamp"})
    entries["change"] = entries["position_fraction"]
    exits = allocated.rename(columns={"exit_timestamp": "timestamp"})
    exits["change"] = -exits["position_fraction"]
    events = pd.concat(
        [
            entries[["variant_id", "symbol", "timestamp", "change"]],
            exits[["variant_id", "symbol", "timestamp", "change"]],
        ],
        ignore_index=True,
    )
    # Grouping entry and exit changes at the same clock implements the frozen
    # convention that positions ending at a timestamp free capacity there.
    gross_changes = (
        events.groupby(["variant_id", "timestamp"], sort=True)["change"]
        .sum()
        .reset_index()
    )
    gross_changes["exposure"] = gross_changes.groupby("variant_id")[
        "change"
    ].cumsum()
    symbol_changes = (
        events.groupby(
            ["variant_id", "symbol", "timestamp"], sort=True
        )["change"]
        .sum()
        .reset_index()
    )
    symbol_changes["exposure"] = symbol_changes.groupby(
        ["variant_id", "symbol"]
    )["change"].cumsum()
    return (
        float(gross_changes["exposure"].max()),
        float(symbol_changes["exposure"].max()),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--valid-dir", type=Path, required=True)
    parser.add_argument("--invalid-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    valid_metrics = pd.read_parquet(
        args.valid_dir / "variant_metrics.parquet"
    )
    invalid_metrics = pd.read_parquet(
        args.invalid_dir / "variant_metrics.parquet"
    )
    trades = pd.read_parquet(args.valid_dir / "trade_details.parquet")
    daily = pd.read_parquet(args.valid_dir / "daily_pnl.parquet")

    if len(valid_metrics) != 174 or valid_metrics["variant_id"].nunique() != 174:
        raise RuntimeError("Valid metric table does not contain 174 variants")
    if daily["date"].max() > CUTOFF:
        raise RuntimeError("Daily P&L crosses the sealed boundary")
    if (trades["position_fraction"] < -TOLERANCE).any():
        raise RuntimeError("Negative allocation detected")
    positive_sizes = trades.loc[
        trades["position_fraction"].gt(0), "position_fraction"
    ]
    if positive_sizes.empty or positive_sizes.min() <= 1e-12:
        raise RuntimeError("Sub-picocapital allocation remains")
    if not np.allclose(
        trades["trade_pnl"],
        trades["unit_net_return"] * trades["position_fraction"],
        atol=TOLERANCE,
        rtol=0,
    ):
        raise RuntimeError("Trade P&L does not reconcile independently")

    trade_totals = trades.groupby("variant_id")["trade_pnl"].sum().sort_index()
    daily_totals = daily.groupby("variant_id")["net_pnl"].sum().sort_index()
    metric_totals = valid_metrics.set_index("variant_id")[
        "total_net_return"
    ].sort_index()
    if not np.allclose(trade_totals, daily_totals, atol=TOLERANCE, rtol=0):
        raise RuntimeError("Trade and daily P&L do not reconcile")
    if not np.allclose(metric_totals, daily_totals, atol=TOLERANCE, rtol=0):
        raise RuntimeError("Metric and daily P&L do not reconcile")

    maximum_gross, maximum_symbol = maximum_concurrent_exposure(trades)
    if maximum_gross > 1.0 + TOLERANCE:
        raise RuntimeError(f"Gross cap exceeded: {maximum_gross}")
    if maximum_symbol > 0.10 + TOLERANCE:
        raise RuntimeError(f"Symbol cap exceeded: {maximum_symbol}")

    keys = ["scope", "selector", "horizon", "cost_bps_per_side"]
    compared = valid_metrics.merge(
        invalid_metrics, on=keys, suffixes=("_valid", "_invalid")
    )
    changed_numeric: dict[str, float] = {}
    for column in valid_metrics.columns:
        if column in keys or column not in invalid_metrics:
            continue
        if not pd.api.types.is_numeric_dtype(valid_metrics[column]):
            continue
        left = compared[f"{column}_valid"].to_numpy(dtype=float)
        right = compared[f"{column}_invalid"].to_numpy(dtype=float)
        finite = np.isfinite(left) & np.isfinite(right)
        difference = (
            float(np.max(np.abs(left[finite] - right[finite])))
            if finite.any()
            else 0.0
        )
        if difference > 1e-12:
            changed_numeric[column] = difference
    if set(changed_numeric) - {"allocated_trades"}:
        raise RuntimeError(
            f"Economic columns changed after repair: {changed_numeric}"
        )

    output = {
        "status": "passed",
        "variant_count": int(len(valid_metrics)),
        "minimum_positive_position_fraction": float(positive_sizes.min()),
        "maximum_concurrent_gross_exposure": float(maximum_gross),
        "maximum_concurrent_symbol_exposure": float(maximum_symbol),
        "maximum_absolute_trade_daily_pnl_difference": float(
            np.max(np.abs(trade_totals - daily_totals))
        ),
        "maximum_absolute_metric_daily_pnl_difference": float(
            np.max(np.abs(metric_totals - daily_totals))
        ),
        "invalid_to_valid_changed_numeric_columns": changed_numeric,
        "valid_hashes": {
            name: sha256(args.valid_dir / name)
            for name in (
                "variant_metrics.parquet",
                "trade_details.parquet",
                "daily_pnl.parquet",
                "reconciliation.json",
            )
        },
        "maximum_loaded_date": str(daily["date"].max().date()),
        "holdout_rows_loaded": 0,
    }
    args.output.write_text(
        json.dumps(output, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
