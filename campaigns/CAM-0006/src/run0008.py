from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0006 import max_drawdown_and_recovery


CENTRAL = "vol_high_dollar_q50_range50_nbbo_slip2"
NEIGHBORS = (
    CENTRAL,
    "vol_high_dollar_q50_nbbo_slip2",
    "vol_high_dollar_q67_nbbo_slip2",
    "vol_high_dollar_q50_range50_nbbo_slip0",
    "vol_high_dollar_q50_range50_nbbo_slip5",
)
MONTHS = pd.period_range("2024-11", "2026-04", freq="M")
BLOCKS = (
    ("block_1", pd.Timestamp("2024-11-01"), pd.Timestamp("2025-04-30")),
    ("block_2", pd.Timestamp("2025-05-01"), pd.Timestamp("2025-10-31")),
    ("block_3", pd.Timestamp("2025-11-01"), pd.Timestamp("2026-04-30")),
)


def metrics(frame: pd.DataFrame) -> dict:
    daily = (
        frame.groupby("date", as_index=False)["net_pnl"].sum()
        if not frame.empty
        else pd.DataFrame(columns=["date", "net_pnl"])
    )
    monthly = (
        daily.assign(month=daily["date"].dt.to_period("M"))
        .groupby("month")["net_pnl"]
        .sum()
        .reindex(MONTHS, fill_value=0.0)
    )
    dd, recovery, unresolved = max_drawdown_and_recovery(daily)
    return {
        "event_count": int(len(frame)),
        "symbol_count": int(frame["symbol"].nunique()) if len(frame) else 0,
        "full_net_simple_return": float(monthly.sum()),
        "average_month_18m": float(monthly.mean()),
        "average_month_15m": float(
            monthly[monthly.index >= pd.Period("2025-02")].mean()
        ),
        "average_month_12m": float(
            monthly[monthly.index >= pd.Period("2025-05")].mean()
        ),
        "negative_months_18m": int((monthly < 0).sum()),
        "zero_months_18m": int((monthly == 0).sum()),
        "standard_max_drawdown": dd,
        "max_recovery_days": recovery,
        "recovery_unresolved": unresolved,
    }


def leaveout_table(positions: pd.DataFrame) -> pd.DataFrame:
    rows = [{"scenario": "central", "removed": "none", **metrics(positions)}]
    ranked_days = (
        positions.groupby("date")["net_pnl"].sum().sort_values(ascending=False)
    )
    for count in (1, 5, 10):
        removed = set(ranked_days.head(count).index)
        frame = positions[~positions["date"].isin(removed)]
        rows.append(
            {
                "scenario": f"remove_top_{count}_days",
                "removed": ",".join(str(pd.Timestamp(x).date()) for x in removed),
                **metrics(frame),
            }
        )
    ranked_symbols = (
        positions.groupby("symbol")["net_pnl"].sum().sort_values(ascending=False)
    )
    for count in (1, 5):
        removed = set(ranked_symbols.head(count).index)
        frame = positions[~positions["symbol"].isin(removed)]
        rows.append(
            {
                "scenario": f"remove_top_{count}_symbols",
                "removed": ",".join(sorted(removed)),
                **metrics(frame),
            }
        )
    monthly = (
        positions.assign(month=positions["date"].dt.to_period("M"))
        .groupby("month")["net_pnl"]
        .sum()
        .sort_values(ascending=False)
    )
    for count in (1, 3):
        removed = set(monthly.head(count).index)
        frame = positions[
            ~positions["date"].dt.to_period("M").isin(removed)
        ]
        rows.append(
            {
                "scenario": f"remove_top_{count}_months",
                "removed": ",".join(str(x) for x in sorted(removed)),
                **metrics(frame),
            }
        )
    for block, start, end in BLOCKS:
        frame = positions[~positions["date"].between(start, end)]
        rows.append(
            {
                "scenario": f"leave_out_{block}",
                "removed": f"{start.date()}:{end.date()}",
                **metrics(frame),
            }
        )
    return pd.DataFrame(rows)


def circular_month_bootstrap(
    monthly: np.ndarray, resamples: int = 20_000, block_length: int = 3
) -> pd.DataFrame:
    rng = np.random.default_rng(6008)
    sample_length = len(monthly)
    blocks_needed = int(np.ceil(sample_length / block_length))
    starts = rng.integers(0, sample_length, size=(resamples, blocks_needed))
    offsets = np.arange(block_length)
    indices = (starts[:, :, None] + offsets[None, None, :]) % sample_length
    samples = monthly[indices.reshape(resamples, -1)[:, :sample_length]]
    cumulative = np.cumsum(samples, axis=1)
    equity = 1.0 + cumulative
    peaks = np.maximum.accumulate(np.concatenate([np.ones((resamples, 1)), equity], axis=1), axis=1)[:, 1:]
    drawdowns = (peaks - equity) / peaks
    return pd.DataFrame(
        {
            "resample": np.arange(resamples),
            "total_net_simple_return": samples.sum(axis=1),
            "average_month": samples.mean(axis=1),
            "maximum_monthly_path_drawdown": drawdowns.max(axis=1),
            "negative_months": (samples < 0).sum(axis=1),
            "zero_months": (samples == 0).sum(axis=1),
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", type=Path, required=True)
    parser.add_argument("--monthly", type=Path, required=True)
    parser.add_argument("--blocks", type=Path, required=True)
    parser.add_argument("--positions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    variants = pd.read_csv(args.variants)
    monthly = pd.read_csv(args.monthly)
    blocks = pd.read_csv(args.blocks)
    positions = pd.read_parquet(args.positions)
    positions["date"] = pd.to_datetime(positions["date"])
    central_positions = positions[positions["variant"].eq(CENTRAL)].copy()
    if len(central_positions) != 94:
        raise RuntimeError(f"Expected 94 central events, got {len(central_positions)}")
    neighbors = variants[variants["variant"].isin(NEIGHBORS)].copy()
    if len(neighbors) != len(NEIGHBORS):
        raise RuntimeError("Missing frozen neighbor")
    neighbor_blocks = blocks[blocks["variant"].isin(NEIGHBORS)].copy()
    leaveouts = leaveout_table(central_positions)
    central_monthly = (
        monthly[monthly["variant"].eq(CENTRAL)]
        .set_index("month")["net_pnl"]
        .reindex([str(x) for x in MONTHS])
        .to_numpy(dtype=float)
    )
    bootstrap = circular_month_bootstrap(central_monthly)
    summary = {
        "central_variant": CENTRAL,
        "central_event_count": int(len(central_positions)),
        "central_symbol_count": int(central_positions["symbol"].nunique()),
        "positive_event_fraction": float((central_positions["net_pnl"] > 0).mean()),
        "median_event_return": float(central_positions["net_pnl"].median()),
        "worst_event_return": float(central_positions["net_pnl"].min()),
        "best_event_return": float(central_positions["net_pnl"].max()),
        "bootstrap": {
            "resamples": int(len(bootstrap)),
            "block_length_months": 3,
            "seed": 6008,
            "probability_total_positive": float(
                (bootstrap["total_net_simple_return"] > 0).mean()
            ),
            "probability_average_month_ge_3pct": float(
                (bootstrap["average_month"] >= 0.03).mean()
            ),
            "probability_average_month_ge_5pct": float(
                (bootstrap["average_month"] >= 0.05).mean()
            ),
            "probability_average_month_ge_10pct": float(
                (bootstrap["average_month"] >= 0.10).mean()
            ),
            "probability_monthly_path_drawdown_lt_20pct": float(
                (bootstrap["maximum_monthly_path_drawdown"] < 0.20).mean()
            ),
            "total_return_quantiles": {
                str(q): float(bootstrap["total_net_simple_return"].quantile(q))
                for q in (0.05, 0.25, 0.50, 0.75, 0.95)
            },
            "average_month_quantiles": {
                str(q): float(bootstrap["average_month"].quantile(q))
                for q in (0.05, 0.25, 0.50, 0.75, 0.95)
            },
            "monthly_drawdown_quantiles": {
                str(q): float(
                    bootstrap["maximum_monthly_path_drawdown"].quantile(q)
                )
                for q in (0.50, 0.90, 0.95)
            },
        },
        "holdout_rows_loaded": 0,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    neighbors.to_csv(args.output_dir / "neighbors.csv", index=False)
    neighbor_blocks.to_csv(args.output_dir / "neighbor_blocks.csv", index=False)
    leaveouts.to_csv(args.output_dir / "leaveouts.csv", index=False)
    bootstrap.to_parquet(args.output_dir / "bootstrap.parquet", index=False)
    (args.output_dir / "adversarial_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "command": (
            "python campaigns/CAM-0006/src/run0008.py "
            "--variants campaigns/CAM-0006/artifacts/RUN-0007/variants.csv "
            "--monthly campaigns/CAM-0006/artifacts/RUN-0007/monthly.csv "
            "--blocks campaigns/CAM-0006/artifacts/RUN-0007/blocks.csv "
            "--positions campaigns/CAM-0006/artifacts/RUN-0007/positions.parquet "
            "--output-dir campaigns/CAM-0006/artifacts/RUN-0008"
        ),
        "resolved_defaults": {
            "central_variant": CENTRAL,
            "neighbor_variants": list(NEIGHBORS),
            "bootstrap_resamples": 20_000,
            "bootstrap_block_length_months": 3,
            "bootstrap_seed": 6008,
        },
        "executed_resamples": int(len(bootstrap)),
        "max_loaded_date": str(positions["date"].max().date()),
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(leaveouts.to_string(index=False))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
