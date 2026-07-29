from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0005 import CUTOFF, max_drawdown_and_recovery


SLIPPAGES = (2, 5, 10)
BLOCKS = (
    ("block_1", pd.Timestamp("2024-11-01"), pd.Timestamp("2025-04-30")),
    ("block_2", pd.Timestamp("2025-05-01"), pd.Timestamp("2025-10-31")),
    ("block_3", pd.Timestamp("2025-11-01"), pd.Timestamp("2026-04-30")),
)
ALLOCATION_NAMES = (
    "fixed_1",
    "high_volume_only",
    "low_volume_025",
    "low_volume_050",
    "soxs_050",
    "soxs_075",
    "low_volume_025_soxs_075",
    "signal_tier_q60_050_q67_075_q80_100",
)


def allocation_sizes(frame: pd.DataFrame) -> dict[str, pd.Series]:
    high_volume = frame["volume_high50"].astype(bool)
    inverse = frame["symbol"].eq("SOXS")
    signal_tier = pd.Series(0.50, index=frame.index)
    signal_tier.loc[frame["is_q67"].astype(bool)] = 0.75
    signal_tier.loc[frame["is_q80"].astype(bool)] = 1.00
    return {
        "fixed_1": pd.Series(1.0, index=frame.index),
        "high_volume_only": high_volume.astype(float),
        "low_volume_025": high_volume.map({True: 1.0, False: 0.25}),
        "low_volume_050": high_volume.map({True: 1.0, False: 0.50}),
        "soxs_050": inverse.map({True: 0.50, False: 1.0}),
        "soxs_075": inverse.map({True: 0.75, False: 1.0}),
        "low_volume_025_soxs_075": (
            high_volume.map({True: 1.0, False: 0.25})
            * inverse.map({True: 0.75, False: 1.0})
        ),
        "signal_tier_q60_050_q67_075_q80_100": signal_tier,
    }


def evaluate(
    frame: pd.DataFrame, sizes: dict[str, pd.Series]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows: list[dict] = []
    monthly_rows: list[dict] = []
    block_rows: list[dict] = []
    months = pd.period_range("2024-11", "2026-04", freq="M")
    for allocation, size in sizes.items():
        for slippage in SLIPPAGES:
            selected = frame.copy()
            selected["size"] = size.astype(float)
            if selected["size"].gt(1.0).any() or selected["size"].lt(0).any():
                raise RuntimeError(f"Exposure violation in {allocation}")
            selected["unit_net"] = (
                selected["nbbo_gross_return"] - 2 * slippage / 10_000
            )
            selected["net_pnl"] = selected["size"] * selected["unit_net"]
            active = selected[selected["size"].gt(0)].copy()
            daily = selected.groupby("date", as_index=False)["net_pnl"].sum()
            monthly = (
                daily.assign(month=pd.to_datetime(daily["date"]).dt.to_period("M"))
                .groupby("month")["net_pnl"]
                .sum()
                .reindex(months, fill_value=0.0)
            )
            dd, recovery, unresolved = max_drawdown_and_recovery(daily)
            total = float(daily["net_pnl"].sum())
            variant = f"{allocation}_slip{slippage}"
            rows.append(
                {
                    "variant": variant,
                    "allocation": allocation,
                    "additional_slippage_bps_per_side": slippage,
                    "active_trade_count": int(len(active)),
                    "average_event_exposure_all_q60": float(selected["size"].mean()),
                    "maximum_event_exposure": float(selected["size"].max()),
                    "full_net_simple_return": total,
                    "average_month_18m": float(monthly.mean()),
                    "median_month_18m": float(monthly.median()),
                    "positive_months_18m": int((monthly > 0).sum()),
                    "negative_months_18m": int((monthly < 0).sum()),
                    "zero_months_18m": int((monthly == 0).sum()),
                    "standard_max_drawdown": dd,
                    "max_recovery_days": recovery,
                    "recovery_unresolved": unresolved,
                    "top_5_day_profit_share": (
                        float(daily["net_pnl"].nlargest(5).sum() / total)
                        if total > 0 else np.nan
                    ),
                    "worst_event_pnl": float(selected["net_pnl"].min()),
                    "soxl_net": float(
                        selected.loc[selected["symbol"].eq("SOXL"), "net_pnl"].sum()
                    ),
                    "soxs_net": float(
                        selected.loc[selected["symbol"].eq("SOXS"), "net_pnl"].sum()
                    ),
                }
            )
            for month, pnl in monthly.items():
                monthly_rows.append(
                    {"variant": variant, "month": str(month), "net_pnl": float(pnl)}
                )
            for block, start, end in BLOCKS:
                sub = selected[pd.to_datetime(selected["date"]).between(start, end)]
                block_rows.append(
                    {
                        "variant": variant,
                        "block": block,
                        "net_pnl": float(sub["net_pnl"].sum()),
                        "mean_exposure": float(sub["size"].mean()),
                    }
                )
    return pd.DataFrame(rows), pd.DataFrame(monthly_rows), pd.DataFrame(block_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enriched-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    enriched = pd.read_parquet(args.enriched_path)
    enriched["date"] = pd.to_datetime(enriched["date"])
    enriched["next_session"] = pd.to_datetime(enriched["next_session"])
    if enriched["next_session"].max() > CUTOFF:
        raise RuntimeError("Sealed holdout row loaded")
    frame = enriched[enriched["is_q60"].astype(bool)].copy()
    sizes = allocation_sizes(frame)
    if tuple(sizes) != ALLOCATION_NAMES:
        raise RuntimeError("Allocation contract mismatch")
    variants, monthly, blocks = evaluate(frame, sizes)
    if len(variants) != 24:
        raise RuntimeError(f"Expected 24 variants, got {len(variants)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    blocks.to_csv(args.output_dir / "blocks.csv", index=False)
    contract = {
        "command": (
            "python campaigns/CAM-0005/src/run0011.py "
            "--enriched-path campaigns/CAM-0005/artifacts/RUN-0009/enriched_events.parquet "
            "--output-dir campaigns/CAM-0005/artifacts/RUN-0011"
        ),
        "resolved_defaults": {
            "allocations": list(ALLOCATION_NAMES),
            "additional_slippage_bps_per_side": list(SLIPPAGES),
            "maximum_event_exposure": 1.0,
        },
        "executed_variant_count": int(len(variants)),
        "q60_event_count": int(len(frame)),
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2), encoding="utf-8"
    )
    print(
        variants[variants["additional_slippage_bps_per_side"].eq(5)]
        .sort_values(
            ["average_month_18m", "standard_max_drawdown"],
            ascending=[False, True],
        )
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()
