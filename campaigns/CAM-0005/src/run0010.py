from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0005 import CUTOFF, max_drawdown_and_recovery, rolling_prior_quantile
from run0003 import price


FILTERS = (
    "q50_edge25",
    "q60_edge25",
    "q67_edge25",
    "q60_edge25_volume_high50",
)
COSTS = (5, 10)
PERIODS = {
    "older": (pd.Timestamp("2023-03-01"), pd.Timestamp("2024-10-31")),
    "recent": (pd.Timestamp("2024-11-01"), pd.Timestamp("2026-04-30")),
}


def build_positions(
    features: pd.DataFrame, minutes: pd.DataFrame
) -> pd.DataFrame:
    frame = features[features["pair"].eq("smh")].sort_values("session").copy()
    frame["threshold_q50_ctx"] = rolling_prior_quantile(frame["abs_signal"], 0.50)
    frame["threshold_q60_ctx"] = rolling_prior_quantile(frame["abs_signal"], 0.60)
    frame["threshold_q67_ctx"] = rolling_prior_quantile(frame["abs_signal"], 0.67)
    frame["volume_q50_ctx"] = rolling_prior_quantile(frame["dollar_volume"], 0.50)
    frame["edge25"] = (
        (frame["signal_return"].lt(0) & frame["close_location"].le(0.25))
        | (frame["signal_return"].gt(0) & frame["close_location"].ge(0.75))
    )
    rows: list[dict] = []
    for item in frame.itertuples():
        if item.session < pd.Timestamp("2023-03-01"):
            continue
        symbol = "SOXL" if item.signal_return < 0 else "SOXS"
        entry = price(minutes, symbol, item.session, "15:59", "open")
        exit_ = price(minutes, symbol, item.next_session, "09:34", "close")
        if entry is None or exit_ is None:
            continue
        gross = exit_ / entry - 1
        memberships = {
            "q50_edge25": item.edge25
            and item.abs_signal >= item.threshold_q50_ctx,
            "q60_edge25": item.edge25
            and item.abs_signal >= item.threshold_q60_ctx,
            "q67_edge25": item.edge25
            and item.abs_signal >= item.threshold_q67_ctx,
            "q60_edge25_volume_high50": item.edge25
            and item.abs_signal >= item.threshold_q60_ctx
            and item.dollar_volume >= item.volume_q50_ctx,
        }
        for filter_name, active in memberships.items():
            if not active:
                continue
            for cost in COSTS:
                rows.append(
                    {
                        "date": item.session,
                        "next_session": item.next_session,
                        "filter": filter_name,
                        "cost_bps_per_side": cost,
                        "symbol": symbol,
                        "signal_return": item.signal_return,
                        "gross_return": gross,
                        "net_pnl": gross - 2 * cost / 10_000,
                    }
                )
    return pd.DataFrame(rows)


def summarize(
    positions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict] = []
    monthly_rows: list[dict] = []
    for filter_name in FILTERS:
        for cost in COSTS:
            variant = f"{filter_name}_c{cost}"
            base = positions[
                positions["filter"].eq(filter_name)
                & positions["cost_bps_per_side"].eq(cost)
            ].copy()
            record = {
                "variant": variant,
                "filter": filter_name,
                "cost_bps_per_side": cost,
            }
            for label, (start, end) in PERIODS.items():
                frame = base[pd.to_datetime(base["date"]).between(start, end)].copy()
                daily = frame.groupby("date", as_index=False)["net_pnl"].sum()
                months = pd.period_range(start.to_period("M"), end.to_period("M"), freq="M")
                monthly = (
                    daily.assign(month=pd.to_datetime(daily["date"]).dt.to_period("M"))
                    .groupby("month")["net_pnl"]
                    .sum()
                    .reindex(months, fill_value=0.0)
                )
                dd, recovery, unresolved = max_drawdown_and_recovery(daily)
                record.update(
                    {
                        f"{label}_trade_count": int(len(frame)),
                        f"{label}_net_simple_return": float(frame["net_pnl"].sum()),
                        f"{label}_average_month": float(monthly.mean()),
                        f"{label}_positive_months": int((monthly > 0).sum()),
                        f"{label}_negative_months": int((monthly < 0).sum()),
                        f"{label}_zero_months": int((monthly == 0).sum()),
                        f"{label}_max_drawdown": dd,
                        f"{label}_max_recovery_days": recovery,
                        f"{label}_recovery_unresolved": unresolved,
                        f"{label}_soxl_net": float(
                            frame.loc[frame["symbol"].eq("SOXL"), "net_pnl"].sum()
                        ),
                        f"{label}_soxs_net": float(
                            frame.loc[frame["symbol"].eq("SOXS"), "net_pnl"].sum()
                        ),
                    }
                )
                for month, pnl in monthly.items():
                    monthly_rows.append(
                        {
                            "variant": variant,
                            "period": label,
                            "month": str(month),
                            "net_pnl": float(pnl),
                        }
                    )
            rows.append(record)
    return pd.DataFrame(rows), pd.DataFrame(monthly_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features-path", type=Path, required=True)
    parser.add_argument("--minutes-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    features = pd.read_parquet(args.features_path)
    minutes = pd.read_parquet(args.minutes_path)
    features["session"] = pd.to_datetime(features["session"])
    features["next_session"] = pd.to_datetime(features["next_session"])
    minutes["session"] = pd.to_datetime(minutes["session"])
    if features["next_session"].max() > CUTOFF or minutes["session"].max() > CUTOFF:
        raise RuntimeError("Sealed holdout row loaded")
    positions = build_positions(features, minutes)
    variants, monthly = summarize(positions)
    if len(variants) != 8:
        raise RuntimeError(f"Expected 8 variants, got {len(variants)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    positions.to_parquet(args.output_dir / "positions.parquet", index=False)
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    contract = {
        "command": (
            "python campaigns/CAM-0005/src/run0010.py "
            "--features-path campaigns/CAM-0005/artifacts/RUN-0002/features.parquet "
            "--minutes-path campaigns/CAM-0005/artifacts/readiness/targeted_minutes.parquet "
            "--output-dir campaigns/CAM-0005/artifacts/RUN-0010"
        ),
        "resolved_defaults": {
            "filters": list(FILTERS),
            "cost_bps_per_side": list(COSTS),
            "older_context": ["2023-03-01", "2024-10-31"],
            "recent_context": ["2024-11-01", "2026-04-30"],
            "entry": "15:59_open",
            "exit": "next_09:34_close",
            "execution_label": "bar_stage_context_only",
        },
        "executed_variant_count": int(len(variants)),
        "max_loaded_date": str(minutes["session"].max().date()),
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2), encoding="utf-8"
    )
    print(variants.to_string(index=False))


if __name__ == "__main__":
    main()
