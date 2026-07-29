from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from cam0008 import CUTOFF
from run0001 import classify, metrics, prepare_candidates, sha256, simulate
from run0003 import exposure_statistics


PORTFOLIOS = (
    "positive_continuation_long",
    "negative_failure_long",
    "all_longs",
)
WINDOWS = (1, 5, 15, 30)
LATENCIES = (0, 1, 3, 5)
HORIZONS = ("five_close", "ten_close")
COSTS = (10, 20)
POSITION_CAPS = (0.02, 0.05, 0.10)


def minute_number(text: str) -> int:
    hour, minute = map(int, text.split(":"))
    return hour * 60 + minute


def minute_text(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def build_timing_frame(
    readiness: pd.DataFrame,
    minute_groups: dict[tuple[str, pd.Timestamp], pd.DataFrame],
    window: int,
    latency: int,
) -> pd.DataFrame:
    rows: list[dict] = []
    for event in readiness.itertuples(index=False):
        row = event._asdict()
        date = pd.Timestamp(event.entry_session)
        path = minute_groups.get((str(event.symbol), date))
        start = minute_number(str(event.reaction_start_minute))
        entry_number = start + window + latency
        entry_minute = minute_text(entry_number)
        final_minute = str(event.final_exit_minute)
        row["entry_minute"] = entry_minute
        row["signal_complete"] = False
        row["same_day_complete"] = False
        if (
            path is None
            or not bool(event.market_day_available)
            or entry_number >= minute_number(final_minute)
            or not np.isfinite(event.split_factor)
            or not np.isfinite(event.prior_close_split)
            or not np.isfinite(event.prior20_median_dollar_volume)
        ):
            rows.append(row)
            continue
        expected_minutes = [
            minute_text(start + offset) for offset in range(window)
        ]
        if (
            entry_minute not in path.index
            or "09:30" not in path.index
            or not all(value in path.index for value in expected_minutes)
        ):
            rows.append(row)
            continue
        reaction = path.loc[expected_minutes]
        entry = path.loc[entry_minute]
        open_bar = path.loc["09:30"]
        factor = float(event.split_factor)
        reaction_open = float(reaction.iloc[0]["open"])
        reaction_close = float(reaction.iloc[-1]["close"])
        reaction_high = float(reaction["high"].max())
        reaction_low = float(reaction["low"].min())
        reaction_range = reaction_high - reaction_low
        reaction_dollar_volume = float(
            (reaction["volume"] * reaction["vwap"]).sum()
        )
        entry_raw = float(entry["open"])
        row.update(
            {
                "signal_complete": True,
                "reaction_observed_rows": int(len(reaction)),
                "reaction_open_raw": reaction_open,
                "reaction_close_raw": reaction_close,
                "reaction_high_raw": reaction_high,
                "reaction_low_raw": reaction_low,
                "reaction_return": reaction_close / reaction_open - 1,
                "action_aligned_reaction": (
                    reaction_close / reaction_open - 1
                )
                * int(event.action_sign),
                "reaction_close_location": (
                    (reaction_close - reaction_low) / reaction_range
                    if reaction_range > 0
                    else np.nan
                ),
                "reaction_volume": float(reaction["volume"].sum()),
                "reaction_trade_count": float(
                    reaction["trade_count"].sum()
                ),
                "reaction_dollar_volume": reaction_dollar_volume,
                "reaction_dollar_participation": (
                    reaction_dollar_volume
                    / float(event.prior20_median_dollar_volume)
                ),
                "pre_reaction_return": float(
                    reaction_open / float(open_bar["open"]) - 1
                ),
                "entry_raw": entry_raw,
                "entry_split": entry_raw * factor,
            }
        )
        rows.append(row)
    frame = pd.DataFrame(rows)
    frame["leg"] = frame.apply(
        lambda row: classify(row) if row["signal_complete"] else None,
        axis=1,
    )
    return frame


def reproduce_parent(
    metrics_frame: pd.DataFrame, parent: pd.DataFrame
) -> float:
    child = metrics_frame[
        metrics_frame["reaction_window_minutes"].eq(5)
        & metrics_frame["entry_latency_minutes"].eq(0)
    ].copy()
    parent = parent[
        parent["screen_id"].eq("baseline")
        & parent["position_cap"].isin(POSITION_CAPS)
    ].copy()
    keys = [
        "selector",
        "horizon",
        "cost_bps_per_side",
        "position_cap",
    ]
    merged = child.merge(parent, on=keys, suffixes=("_child", "_parent"))
    if len(merged) != 36:
        raise RuntimeError(
            f"Comparable parent reproduction has {len(merged)} rows, expected 36"
        )
    numeric_columns = [
        "candidate_events",
        "allocated_trades",
        "symbols",
        "total_net_return",
        "maximum_drawdown",
        "full_average_month",
        "recent_18m_average_month",
        "recent_15m_average_month",
        "recent_12m_average_month",
        "block_1_2024h2_net_return",
        "block_2_2025h1_net_return",
        "block_3_2025h2_net_return",
        "block_4_2026ytd_net_return",
    ]
    maximum = 0.0
    for column in numeric_columns:
        difference = np.max(
            np.abs(
                merged[f"{column}_child"].to_numpy(dtype=float)
                - merged[f"{column}_parent"].to_numpy(dtype=float)
            )
        )
        maximum = max(maximum, float(difference))
    if maximum > 1e-10:
        raise RuntimeError(
            f"Five-minute zero-latency parent reproduction failed: {maximum}"
        )
    return maximum


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record", type=Path, required=True)
    parser.add_argument("--event-readiness", type=Path, required=True)
    parser.add_argument("--event-minutes", type=Path, required=True)
    parser.add_argument("--daily-split", type=Path, required=True)
    parser.add_argument("--parent-metrics", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    record = yaml.safe_load(args.run_record.read_text(encoding="utf-8"))
    frozen = record["frozen_configuration"]
    if record["status"] != "frozen":
        raise RuntimeError("RUN-0004 record is not frozen")
    if tuple(frozen["portfolios"]) != PORTFOLIOS:
        raise RuntimeError("Portfolio list differs from frozen record")
    if tuple(frozen["reaction_windows_minutes"]) != WINDOWS:
        raise RuntimeError("Reaction windows differ from frozen record")
    if tuple(frozen["entry_latency_minutes_after_reaction"]) != LATENCIES:
        raise RuntimeError("Latencies differ from frozen record")
    if frozen["expected_variant_count"]["total"] != 576:
        raise RuntimeError("Frozen variant count is not 576")

    readiness = pd.read_parquet(args.event_readiness)
    minutes = pd.read_parquet(args.event_minutes)
    daily_prices = pd.read_parquet(args.daily_split)
    readiness["entry_session"] = pd.to_datetime(readiness["entry_session"])
    minutes["date"] = pd.to_datetime(minutes["date"])
    daily_prices["date"] = pd.to_datetime(daily_prices["date"])
    if max(
        readiness["entry_session"].max(),
        minutes["date"].max(),
        daily_prices["date"].max(),
    ) > CUTOFF:
        raise RuntimeError("RUN-0004 input crosses sealed boundary")
    if minutes.duplicated(["symbol", "date", "minute"]).any():
        raise RuntimeError("Duplicate minute key")
    minute_groups = {
        (str(symbol), pd.Timestamp(date)): frame.sort_values("minute").set_index(
            "minute", drop=False
        )
        for (symbol, date), frame in minutes.groupby(["symbol", "date"])
    }
    sessions = pd.DatetimeIndex(
        sorted(daily_prices["date"].drop_duplicates())
    )

    metric_rows: list[dict] = []
    trade_frames: list[pd.DataFrame] = []
    daily_frames: list[pd.DataFrame] = []
    attrition: dict[str, dict] = {}
    for window in WINDOWS:
        for latency in LATENCIES:
            timing = build_timing_frame(
                readiness, minute_groups, window, latency
            )
            eligible = timing[
                timing["signal_complete"]
                & timing["prior20_median_dollar_volume"].ge(100_000_000)
                & timing["leg"].isin(
                    [
                        "positive_continuation_long",
                        "negative_failure_long",
                    ]
                )
            ].copy()
            timing_key = f"window{window}|latency{latency}"
            attrition[timing_key] = {
                "registry_events": int(len(timing)),
                "signal_complete_events": int(timing["signal_complete"].sum()),
                "liquidity_eligible_positive_reaction_events": int(
                    len(eligible)
                ),
                "positive_continuation_long": int(
                    eligible["leg"].eq("positive_continuation_long").sum()
                ),
                "negative_failure_long": int(
                    eligible["leg"].eq("negative_failure_long").sum()
                ),
            }
            for portfolio in PORTFOLIOS:
                portfolio_frame = (
                    eligible[eligible["leg"].eq(portfolio)]
                    if portfolio != "all_longs"
                    else eligible
                )
                for horizon in HORIZONS:
                    for cost in COSTS:
                        candidates = prepare_candidates(
                            portfolio_frame, portfolio, horizon, cost
                        )
                        for cap in POSITION_CAPS:
                            cap_label = f"{cap:.2f}".rstrip("0").rstrip(".")
                            variant_id = (
                                f"{portfolio}__w{window}__l{latency}"
                                f"__{horizon}__{cost}bp__cap{cap_label}"
                            )
                            trades, daily_pnl = simulate(
                                candidates,
                                daily_prices,
                                sessions,
                                cost,
                                cap,
                            )
                            trades["variant_id"] = variant_id
                            daily_pnl["variant_id"] = variant_id
                            row = metrics(
                                variant_id,
                                "all_events",
                                portfolio,
                                horizon,
                                cost,
                                trades,
                                daily_pnl,
                            )
                            row.update(
                                {
                                    "reaction_window_minutes": window,
                                    "entry_latency_minutes": latency,
                                    "position_cap": cap,
                                }
                            )
                            row.update(exposure_statistics(trades, sessions))
                            metric_rows.append(row)
                            trade_frames.append(trades)
                            daily_frames.append(daily_pnl)

    metric_frame = pd.DataFrame(metric_rows)
    if (
        len(metric_frame) != 576
        or metric_frame["variant_id"].nunique() != 576
    ):
        raise RuntimeError(
            f"Executed {len(metric_frame)} variants, expected 576"
        )
    trades = pd.concat(trade_frames, ignore_index=True)
    daily_pnl = pd.concat(daily_frames, ignore_index=True)
    daily_totals = daily_pnl.groupby("variant_id")["net_pnl"].sum().sort_index()
    trade_totals = (
        trades.groupby("variant_id")["trade_pnl"]
        .sum()
        .reindex(daily_totals.index, fill_value=0.0)
    )
    if not np.allclose(trade_totals, daily_totals):
        raise RuntimeError("RUN-0004 aggregate P&L reconciliation failed")
    if metric_frame["maximum_actual_gross"].max() > 1.0 + 1e-10:
        raise RuntimeError("Maximum gross exposure exceeded")
    if metric_frame["maximum_actual_symbol_gross"].max() > 0.10 + 1e-10:
        raise RuntimeError("Maximum symbol exposure exceeded")

    parent = pd.read_parquet(args.parent_metrics)
    reproduction_difference = reproduce_parent(metric_frame, parent)
    metric_frame = metric_frame.sort_values(
        ["recent_15m_average_month", "maximum_drawdown"],
        ascending=[False, True],
    ).reset_index(drop=True)
    metric_frame.to_parquet(
        args.output_dir / "variant_metrics.parquet", index=False
    )
    trades.to_parquet(args.output_dir / "trade_details.parquet", index=False)
    daily_pnl.to_parquet(args.output_dir / "daily_pnl.parquet", index=False)
    (args.output_dir / "attrition.json").write_text(
        json.dumps(
            {
                "timing_counts": attrition,
                "maximum_loaded_date": "2026-04-30",
                "holdout_rows_loaded": 0,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    frozen_hash = hashlib.sha256(
        json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    reconciliation = {
        "status": "passed",
        "run_id": "RUN-0004",
        "expected_variant_count": 576,
        "executed_variant_count": int(len(metric_frame)),
        "resolved_portfolios": list(PORTFOLIOS),
        "resolved_reaction_windows": list(WINDOWS),
        "resolved_entry_latencies": list(LATENCIES),
        "resolved_horizons": list(HORIZONS),
        "resolved_costs": list(COSTS),
        "resolved_position_caps": list(POSITION_CAPS),
        "parent_reproduction_rows": 36,
        "parent_reproduction_maximum_numeric_difference": (
            reproduction_difference
        ),
        "frozen_configuration_hash": frozen_hash,
        "input_hashes": {
            "event_readiness": sha256(args.event_readiness),
            "event_minutes": sha256(args.event_minutes),
            "daily_split": sha256(args.daily_split),
            "parent_metrics": sha256(args.parent_metrics),
        },
        "executed_code_hashes": {
            Path(__file__).name: sha256(Path(__file__)),
            "run0003.py": sha256(Path(__file__).with_name("run0003.py")),
            "run0001.py": sha256(Path(__file__).with_name("run0001.py")),
            "cam0008.py": sha256(Path(__file__).with_name("cam0008.py")),
        },
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2) + "\n", encoding="utf-8"
    )
    summary_columns = [
        "variant_id",
        "selector",
        "reaction_window_minutes",
        "entry_latency_minutes",
        "horizon",
        "cost_bps_per_side",
        "position_cap",
        "candidate_events",
        "allocated_trades",
        "symbols",
        "average_close_gross",
        "recent_15m_average_month",
        "recent_12m_average_month",
        "full_average_month",
        "maximum_drawdown",
        "recovery_days",
        "recent_15m_positive_months",
        "recent_15m_negative_months",
        "recent_15m_inactive_months",
        "block_1_2024h2_net_return",
        "block_2_2025h1_net_return",
        "block_3_2025h2_net_return",
        "block_4_2026ytd_net_return",
        "top5_event_positive_share",
        "top5_day_positive_share",
        "top_symbol",
        "top_firm",
    ]
    top = metric_frame.head(60)[summary_columns]
    (args.output_dir / "summary.json").write_text(
        json.dumps(
            {
                "status": "completed_uninterpreted",
                "variant_count": int(len(metric_frame)),
                "top_60": top.where(pd.notna(top), None).to_dict("records"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
