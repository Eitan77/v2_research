from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from cam0007 import CUTOFF
from run0001 import allocate_overlapping, build_metrics


ENTRY_MINUTES = ("10:00", "10:01", "10:03", "10:05")
COSTS = (5, 10, 20)
POSITION_CAPS = (0.10, 0.20, 0.33, 0.50)
SINGLE_HORIZONS = tuple(range(2, 11))
POSITIVE_COMBINED_HORIZONS = (4, 5, 6)
NEGATIVE_COMBINED_HORIZONS = (8, 9, 10)
SLEEVES = (
    "positive_after_close_continuation",
    "negative_high_vol_reclaim",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_candidates(
    frame: pd.DataFrame,
    sleeve: str,
    entry_minute: str,
    horizon: int,
    sessions: pd.DatetimeIndex,
    close_lookup: pd.Series,
) -> pd.DataFrame:
    if sleeve == "positive_after_close_continuation":
        mask = (
            frame["gap_return"].ge(0.02)
            & frame["first30_return"].gt(0)
            & frame["announcement_bucket"].eq("after_close")
        )
    elif sleeve == "negative_high_vol_reclaim":
        mask = (
            frame["gap_return"].le(-0.02)
            & frame["first30_return"].gt(0)
            & frame["stock_vol_high"].eq(True)
        )
    else:
        raise KeyError(sleeve)
    selected = frame[mask].copy()
    entry_column = f"entry_{entry_minute.replace(':', '')}_split"
    selected["entry_price"] = selected[entry_column]
    session_number = {pd.Timestamp(date): index for index, date in enumerate(sessions)}
    exits = []
    exit_prices = []
    offset = horizon - 1
    for row in selected.itertuples(index=False):
        start = session_number.get(pd.Timestamp(row.entry_session))
        target_index = None if start is None else start + offset
        if target_index is None or target_index >= len(sessions):
            exits.append(pd.NaT)
            exit_prices.append(np.nan)
            continue
        target = pd.Timestamp(sessions[target_index])
        if target > CUTOFF or (row.symbol, target) not in close_lookup.index:
            exits.append(pd.NaT)
            exit_prices.append(np.nan)
            continue
        exits.append(target)
        exit_prices.append(float(close_lookup.loc[(row.symbol, target)]))
    selected["exit_session"] = pd.to_datetime(exits)
    selected["exit_price"] = exit_prices
    selected["exit_timing"] = "close"
    selected["sleeve"] = sleeve
    selected["holding_sessions"] = horizon
    selected["entry_minute"] = entry_minute
    selected = selected[
        selected["entry_price"].notna()
        & selected["exit_session"].notna()
        & selected["exit_price"].notna()
    ].copy()
    return selected


def simulate(
    candidates: pd.DataFrame,
    daily_prices: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    session_number: dict[pd.Timestamp, int],
    cost: int,
    position_cap: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    trades = candidates.copy()
    trades["event_id"] = (
        trades["symbol"] + "|" + trades["event_timestamp"].astype(str)
    )
    trades["entry_1000_split"] = trades["entry_price"]
    trades["unit_net_return"] = (
        trades["exit_price"] / trades["entry_price"]
        - 1.0
        - 2.0 * cost / 10_000.0
    )
    trades["stopped"] = False
    trades = allocate_overlapping(trades, session_number, position_cap)
    trades["trade_pnl"] = (
        trades["unit_net_return"] * trades["position_fraction"]
    )
    close_lookup = daily_prices.set_index(["symbol", "date"])["close"]
    pnl: defaultdict[pd.Timestamp, float] = defaultdict(float)
    for row in trades[trades["position_fraction"].gt(0)].itertuples(index=False):
        size = float(row.position_fraction)
        entry = float(row.entry_price)
        entry_date = pd.Timestamp(row.entry_session)
        exit_date = pd.Timestamp(row.exit_session)
        entry_index = session_number[entry_date]
        exit_index = session_number[exit_date]
        entry_close = float(close_lookup.loc[(row.symbol, entry_date)])
        pnl[entry_date] += size * (
            (entry_close - entry) / entry - cost / 10_000.0
        )
        previous_close = entry_close
        for index in range(entry_index + 1, exit_index + 1):
            date = pd.Timestamp(sessions[index])
            current_close = float(close_lookup.loc[(row.symbol, date)])
            increment = (current_close - previous_close) / entry
            if date == exit_date:
                increment -= cost / 10_000.0
            pnl[date] += size * increment
            previous_close = current_close
    daily = pd.DataFrame({"date": sessions})
    daily["net_pnl"] = daily["date"].map(pnl).fillna(0.0)
    if not np.isclose(daily["net_pnl"].sum(), trades["trade_pnl"].sum()):
        raise RuntimeError("Trade and marked-to-market P&L do not reconcile")
    return trades, daily


def add_leave_one_out(metrics: dict, trades: pd.DataFrame) -> None:
    allocated = trades[trades["position_fraction"].gt(0)]
    if allocated.empty:
        metrics.update(
            {
                "full_return_without_top_symbol": 0.0,
                "full_return_without_top_event": 0.0,
                "recent15_trade_return_without_top_symbol": 0.0,
                "recent15_trade_return_without_top_event": 0.0,
            }
        )
        return
    by_symbol = allocated.groupby("symbol")["trade_pnl"].sum()
    total = float(allocated["trade_pnl"].sum())
    recent = allocated[
        allocated["entry_session"].ge(pd.Timestamp("2025-02-01"))
    ]
    recent_by_symbol = recent.groupby("symbol")["trade_pnl"].sum()
    metrics["full_return_without_top_symbol"] = float(
        total - by_symbol.max()
    )
    metrics["full_return_without_top_event"] = float(
        total - allocated["trade_pnl"].max()
    )
    metrics["recent15_trade_return_without_top_symbol"] = float(
        recent["trade_pnl"].sum()
        - (recent_by_symbol.max() if len(recent_by_symbol) else 0.0)
    )
    metrics["recent15_trade_return_without_top_event"] = float(
        recent["trade_pnl"].sum()
        - (recent["trade_pnl"].max() if len(recent) else 0.0)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record", type=Path, required=True)
    parser.add_argument("--event-readiness", type=Path, required=True)
    parser.add_argument("--event-minutes", type=Path, required=True)
    parser.add_argument("--causal-features", type=Path, required=True)
    parser.add_argument("--daily-split", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    record = yaml.safe_load(args.run_record.read_text(encoding="utf-8"))
    if record["status"] != "frozen":
        raise RuntimeError("RUN-0003 record is not frozen")
    if record["frozen_configuration"]["expected_variant_count"]["total"] != 1296:
        raise RuntimeError("Frozen variant count is not 1296")

    readiness = pd.read_parquet(args.event_readiness)
    minutes = pd.read_parquet(args.event_minutes)
    features = pd.read_parquet(args.causal_features)
    daily = pd.read_parquet(args.daily_split)
    readiness["entry_session"] = pd.to_datetime(readiness["entry_session"])
    minutes["date"] = pd.to_datetime(minutes["date"])
    features["entry_session"] = pd.to_datetime(features["entry_session"])
    daily["date"] = pd.to_datetime(daily["date"])
    if max(
        readiness["entry_session"].max(),
        minutes["date"].max(),
        features["entry_session"].max(),
        daily["date"].max(),
    ) > CUTOFF:
        raise RuntimeError("RUN-0003 input crosses sealed boundary")

    state_columns = [
        "symbol",
        "event_timestamp",
        "stock_vol20",
        "stock_vol_prior60_median",
        "stock_vol_high",
    ]
    frame = readiness.merge(
        features[state_columns],
        on=["symbol", "event_timestamp"],
        how="left",
        validate="one_to_one",
    )
    entry_rows = minutes[minutes["minute"].isin(ENTRY_MINUTES)].copy()
    if entry_rows.duplicated(["symbol", "date", "minute"]).any():
        raise RuntimeError("Duplicate latency minute")
    entries = entry_rows.pivot(
        index=["symbol", "date"], columns="minute", values="open"
    ).reset_index()
    entries = entries.rename(
        columns={
            minute: f"latency_entry_{minute.replace(':', '')}_raw"
            for minute in ENTRY_MINUTES
        }
    )
    frame = frame.merge(
        entries,
        left_on=["symbol", "entry_session"],
        right_on=["symbol", "date"],
        how="left",
        validate="one_to_one",
    ).drop(columns=["date"])
    matched_1000 = frame[
        frame["entry_1000_raw"].notna()
        & frame["latency_entry_1000_raw"].notna()
    ]
    if not np.allclose(
        matched_1000["entry_1000_raw"],
        matched_1000["latency_entry_1000_raw"],
    ):
        raise RuntimeError("Reloaded 10:00 entry differs from readiness")
    for minute in ENTRY_MINUTES:
        raw_column = f"latency_entry_{minute.replace(':', '')}_raw"
        frame[f"entry_{minute.replace(':', '')}_split"] = (
            frame[raw_column] * frame["split_factor"]
        )
    frame = frame[
        frame["signal_complete"]
        & frame["prior20_median_dollar_volume"].ge(100_000_000)
    ].copy()
    sessions = pd.DatetimeIndex(sorted(daily["date"].drop_duplicates()))
    session_number = {pd.Timestamp(date): index for index, date in enumerate(sessions)}
    close_lookup = daily.set_index(["symbol", "date"])["close"]

    cache: dict[tuple[str, str, int], pd.DataFrame] = {}
    candidate_counts = {}
    for sleeve in SLEEVES:
        for entry_minute in ENTRY_MINUTES:
            for horizon in SINGLE_HORIZONS:
                key = (sleeve, entry_minute, horizon)
                cache[key] = make_candidates(
                    frame,
                    sleeve,
                    entry_minute,
                    horizon,
                    sessions,
                    close_lookup,
                )
                candidate_counts["|".join(map(str, key))] = int(len(cache[key]))

    metric_rows = []
    trade_frames = []
    daily_frames = []
    for sleeve in SLEEVES:
        for horizon in SINGLE_HORIZONS:
            for entry_minute in ENTRY_MINUTES:
                candidates = cache[(sleeve, entry_minute, horizon)]
                for cost in COSTS:
                    for cap in POSITION_CAPS:
                        cap_label = int(round(cap * 100))
                        variant_id = (
                            f"single__{sleeve}__h{horizon}__"
                            f"{entry_minute.replace(':', '')}__{cost}bp__cap{cap_label}"
                        )
                        trades, daily_pnl = simulate(
                            candidates,
                            daily,
                            sessions,
                            session_number,
                            cost,
                            cap,
                        )
                        trades["variant_id"] = variant_id
                        daily_pnl["variant_id"] = variant_id
                        metrics = build_metrics(
                            variant_id,
                            sleeve,
                            f"{horizon}_session_close",
                            cost,
                            trades,
                            daily_pnl,
                        )
                        metrics.update(
                            {
                                "book": "single",
                                "sleeve": sleeve,
                                "positive_horizon": horizon
                                if sleeve == SLEEVES[0]
                                else np.nan,
                                "negative_horizon": horizon
                                if sleeve == SLEEVES[1]
                                else np.nan,
                                "entry_minute": entry_minute,
                                "position_cap": cap,
                            }
                        )
                        add_leave_one_out(metrics, trades)
                        metric_rows.append(metrics)
                        trade_frames.append(trades)
                        daily_frames.append(daily_pnl)
    for positive_horizon in POSITIVE_COMBINED_HORIZONS:
        for negative_horizon in NEGATIVE_COMBINED_HORIZONS:
            for entry_minute in ENTRY_MINUTES:
                candidates = pd.concat(
                    [
                        cache[
                            (
                                "positive_after_close_continuation",
                                entry_minute,
                                positive_horizon,
                            )
                        ],
                        cache[
                            (
                                "negative_high_vol_reclaim",
                                entry_minute,
                                negative_horizon,
                            )
                        ],
                    ],
                    ignore_index=True,
                )
                for cost in COSTS:
                    for cap in POSITION_CAPS:
                        cap_label = int(round(cap * 100))
                        variant_id = (
                            f"combined__p{positive_horizon}_n{negative_horizon}__"
                            f"{entry_minute.replace(':', '')}__{cost}bp__cap{cap_label}"
                        )
                        trades, daily_pnl = simulate(
                            candidates,
                            daily,
                            sessions,
                            session_number,
                            cost,
                            cap,
                        )
                        trades["variant_id"] = variant_id
                        daily_pnl["variant_id"] = variant_id
                        metrics = build_metrics(
                            variant_id,
                            "combined",
                            f"p{positive_horizon}_n{negative_horizon}",
                            cost,
                            trades,
                            daily_pnl,
                        )
                        metrics.update(
                            {
                                "book": "combined",
                                "sleeve": "combined",
                                "positive_horizon": positive_horizon,
                                "negative_horizon": negative_horizon,
                                "entry_minute": entry_minute,
                                "position_cap": cap,
                            }
                        )
                        add_leave_one_out(metrics, trades)
                        metric_rows.append(metrics)
                        trade_frames.append(trades)
                        daily_frames.append(daily_pnl)
    metrics = pd.DataFrame(metric_rows)
    if len(metrics) != 1296 or metrics["variant_id"].nunique() != 1296:
        raise RuntimeError(f"Executed {len(metrics)} variants, expected 1296")
    if metrics["maximum_gross_exposure"].max() > 1.00000001:
        raise RuntimeError("Gross exposure exceeded 1.0")
    trades = pd.concat(trade_frames, ignore_index=True)
    daily_pnl = pd.concat(daily_frames, ignore_index=True)
    if not np.allclose(
        trades.groupby("variant_id")["trade_pnl"].sum().sort_index(),
        daily_pnl.groupby("variant_id")["net_pnl"].sum().sort_index(),
    ):
        raise RuntimeError("RUN-0003 aggregate reconciliation failed")

    metrics = metrics.sort_values(
        ["recent_15m_average_month", "maximum_drawdown"],
        ascending=[False, True],
    ).reset_index(drop=True)
    metrics.to_parquet(args.output_dir / "variant_metrics.parquet", index=False)
    trades.to_parquet(args.output_dir / "trade_details.parquet", index=False)
    daily_pnl.to_parquet(args.output_dir / "daily_pnl.parquet", index=False)
    attrition = {
        "signal_liquidity_ready_events": int(len(frame)),
        "candidate_counts": candidate_counts,
        "entry_minute_complete_counts": {
            minute: int(frame[f"entry_{minute.replace(':', '')}_split"].notna().sum())
            for minute in ENTRY_MINUTES
        },
        "maximum_loaded_date": str(
            max(
                readiness["entry_session"].max(),
                minutes["date"].max(),
                daily["date"].max(),
            ).date()
        ),
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "attrition.json").write_text(
        json.dumps(attrition, indent=2) + "\n", encoding="utf-8"
    )
    frozen_hash = hashlib.sha256(
        json.dumps(
            record["frozen_configuration"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    code_paths = [
        Path(__file__),
        Path(__file__).with_name("run0001.py"),
        Path(__file__).with_name("cam0007.py"),
    ]
    reconciliation = {
        "status": "passed",
        "run_id": "RUN-0003",
        "expected_variant_count": 1296,
        "executed_variant_count": int(len(metrics)),
        "resolved_entry_minutes": list(ENTRY_MINUTES),
        "resolved_single_horizons": list(SINGLE_HORIZONS),
        "resolved_combined_horizons": {
            "positive": list(POSITIVE_COMBINED_HORIZONS),
            "negative": list(NEGATIVE_COMBINED_HORIZONS),
        },
        "resolved_costs_bps_per_side": list(COSTS),
        "resolved_position_caps": list(POSITION_CAPS),
        "frozen_configuration_hash": frozen_hash,
        "input_hashes": {
            "event_readiness": sha256(args.event_readiness),
            "event_minutes": sha256(args.event_minutes),
            "causal_features": sha256(args.causal_features),
            "daily_split": sha256(args.daily_split),
        },
        "executed_code_hashes": {path.name: sha256(path) for path in code_paths},
        "maximum_loaded_date": attrition["maximum_loaded_date"],
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2) + "\n", encoding="utf-8"
    )
    primary = metrics[metrics["cost_bps_per_side"].eq(10)]
    summary_columns = [
        "variant_id",
        "book",
        "sleeve",
        "positive_horizon",
        "negative_horizon",
        "entry_minute",
        "position_cap",
        "allocated_trades",
        "symbols",
        "recent_15m_average_month",
        "recent_12m_average_month",
        "full_average_month",
        "recent_15m_positive_months",
        "recent_15m_negative_months",
        "recent_15m_inactive_months",
        "maximum_drawdown",
        "recovery_days",
        "top5_event_positive_share",
        "top_symbol",
        "full_return_without_top_symbol",
        "full_return_without_top_event",
    ]
    summary = {
        "status": "completed_uninterpreted",
        "variant_count": int(len(metrics)),
        "primary_sort": "10 bp per side, recent 15-month average",
        "top_20_primary": primary[summary_columns]
        .head(20)
        .replace({np.nan: None})
        .to_dict(orient="records"),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
