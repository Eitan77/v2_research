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
from run0001 import build_metrics
from run0003 import add_leave_one_out, make_candidates


ENTRY_MINUTES = ("10:00", "10:01", "10:05")
COSTS = (10, 20)
HORIZONS = (7, 8, 9)
SHARED_CAPS = (0.33, 0.50, 0.67, 1.00)
RESERVED_CAPS = (0.25, 0.50)
SHARED_RULES = ("equal_cohort", "strength_priority")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def allocate_custom(
    candidates: pd.DataFrame,
    session_number: dict[pd.Timestamp, int],
    position_cap: float,
    maximum_gross: float,
    rule: str,
) -> pd.DataFrame:
    result = candidates.sort_values(
        ["entry_session", "symbol", "event_timestamp"]
    ).copy()
    result["position_fraction"] = 0.0
    active: list[tuple[int, float]] = []
    for entry_session, index in result.groupby("entry_session", sort=True).groups.items():
        entry_order = session_number[pd.Timestamp(entry_session)] * 2 + 1
        active = [(end, size) for end, size in active if end > entry_order]
        available = max(0.0, maximum_gross - sum(size for _, size in active))
        if rule == "equal_cohort":
            size = min(position_cap, available / len(index)) if available > 0 else 0.0
            allocations = [(row_index, size) for row_index in index]
        elif rule == "strength_priority":
            ordered = result.loc[index].sort_values(
                ["mechanism_score", "symbol"], ascending=[False, True]
            ).index
            allocations = []
            remaining = available
            for row_index in ordered:
                size = min(position_cap, remaining)
                allocations.append((row_index, size))
                remaining -= size
        else:
            raise KeyError(rule)
        for row_index, size in allocations:
            result.loc[row_index, "position_fraction"] = size
            if size <= 0:
                continue
            row = result.loc[row_index]
            exit_order = session_number[pd.Timestamp(row["exit_session"])] * 2 + 2
            active.append((exit_order, size))
    return result


def simulate_allocated(
    candidates: pd.DataFrame,
    daily_prices: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    session_number: dict[pd.Timestamp, int],
    cost: int,
    cap: float,
    allocation_rule: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    trades = candidates.copy()
    trades["event_id"] = (
        trades["symbol"] + "|" + trades["event_timestamp"].astype(str)
    )
    trades["unit_net_return"] = (
        trades["exit_price"] / trades["entry_price"]
        - 1.0
        - 2.0 * cost / 10_000.0
    )
    trades["stopped"] = False
    if allocation_rule == "reserved_half":
        allocated = []
        for _, sleeve_frame in trades.groupby("sleeve", sort=False):
            allocated.append(
                allocate_custom(
                    sleeve_frame,
                    session_number,
                    cap,
                    0.50,
                    "equal_cohort",
                )
            )
        trades = pd.concat(allocated, ignore_index=True)
    else:
        trades = allocate_custom(
            trades, session_number, cap, 1.0, allocation_rule
        )
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
        previous = entry_close
        for index in range(entry_index + 1, exit_index + 1):
            date = pd.Timestamp(sessions[index])
            current = float(close_lookup.loc[(row.symbol, date)])
            increment = (current - previous) / entry
            if date == exit_date:
                increment -= cost / 10_000.0
            pnl[date] += size * increment
            previous = current
    daily_pnl = pd.DataFrame({"date": sessions})
    daily_pnl["net_pnl"] = daily_pnl["date"].map(pnl).fillna(0.0)
    if not np.isclose(daily_pnl["net_pnl"].sum(), trades["trade_pnl"].sum()):
        raise RuntimeError("RUN-0004 marked-to-market reconciliation failed")
    return trades, daily_pnl


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
        raise RuntimeError("RUN-0004 record is not frozen")
    if record["frozen_configuration"]["expected_variant_count"]["total"] != 540:
        raise RuntimeError("Frozen variant count is not 540")

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
        raise RuntimeError("RUN-0004 input crosses sealed boundary")
    frame = readiness.merge(
        features[
            [
                "symbol",
                "event_timestamp",
                "stock_vol20",
                "stock_vol_prior60_median",
                "stock_vol_high",
            ]
        ],
        on=["symbol", "event_timestamp"],
        how="left",
        validate="one_to_one",
    )
    entry_rows = minutes[minutes["minute"].isin(ENTRY_MINUTES)].copy()
    if entry_rows.duplicated(["symbol", "date", "minute"]).any():
        raise RuntimeError("Duplicate RUN-0004 entry minute")
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
    for minute in ENTRY_MINUTES:
        frame[f"entry_{minute.replace(':', '')}_split"] = (
            frame[f"latency_entry_{minute.replace(':', '')}_raw"]
            * frame["split_factor"]
        )
    frame = frame[
        frame["signal_complete"]
        & frame["prior20_median_dollar_volume"].ge(100_000_000)
    ].copy()
    sessions = pd.DatetimeIndex(sorted(daily["date"].drop_duplicates()))
    session_number = {pd.Timestamp(date): index for index, date in enumerate(sessions)}
    close_lookup = daily.set_index(["symbol", "date"])["close"]

    cache = {}
    candidate_counts = {}
    for sleeve in (
        "positive_after_close_continuation",
        "negative_high_vol_reclaim",
    ):
        for entry_minute in ENTRY_MINUTES:
            for horizon in HORIZONS:
                key = (sleeve, entry_minute, horizon)
                candidates = make_candidates(
                    frame,
                    sleeve,
                    entry_minute,
                    horizon,
                    sessions,
                    close_lookup,
                )
                candidates["mechanism_score"] = (
                    candidates["gap_return"].abs()
                    + candidates["first30_return"]
                )
                cache[key] = candidates
                candidate_counts["|".join(map(str, key))] = int(len(candidates))

    metric_rows = []
    trade_frames = []
    daily_frames = []
    for positive_horizon in HORIZONS:
        for negative_horizon in HORIZONS:
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
                    for rule in SHARED_RULES:
                        for cap in SHARED_CAPS:
                            cap_label = int(round(cap * 100))
                            variant_id = (
                                f"{rule}__p{positive_horizon}_n{negative_horizon}__"
                                f"{entry_minute.replace(':', '')}__{cost}bp__cap{cap_label}"
                            )
                            trades, daily_pnl = simulate_allocated(
                                candidates,
                                daily,
                                sessions,
                                session_number,
                                cost,
                                cap,
                                rule,
                            )
                            trades["variant_id"] = variant_id
                            daily_pnl["variant_id"] = variant_id
                            metrics = build_metrics(
                                variant_id,
                                rule,
                                f"p{positive_horizon}_n{negative_horizon}",
                                cost,
                                trades,
                                daily_pnl,
                            )
                            metrics.update(
                                {
                                    "allocation_rule": rule,
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
                    for cap in RESERVED_CAPS:
                        cap_label = int(round(cap * 100))
                        variant_id = (
                            f"reserved_half__p{positive_horizon}_n{negative_horizon}__"
                            f"{entry_minute.replace(':', '')}__{cost}bp__cap{cap_label}"
                        )
                        trades, daily_pnl = simulate_allocated(
                            candidates,
                            daily,
                            sessions,
                            session_number,
                            cost,
                            cap,
                            "reserved_half",
                        )
                        trades["variant_id"] = variant_id
                        daily_pnl["variant_id"] = variant_id
                        metrics = build_metrics(
                            variant_id,
                            "reserved_half",
                            f"p{positive_horizon}_n{negative_horizon}",
                            cost,
                            trades,
                            daily_pnl,
                        )
                        metrics.update(
                            {
                                "allocation_rule": "reserved_half",
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
    if len(metrics) != 540 or metrics["variant_id"].nunique() != 540:
        raise RuntimeError(f"Executed {len(metrics)} variants, expected 540")
    if metrics["maximum_gross_exposure"].max() > 1.00000001:
        raise RuntimeError("RUN-0004 gross exposure exceeded 1.0")
    trades = pd.concat(trade_frames, ignore_index=True)
    daily_pnl = pd.concat(daily_frames, ignore_index=True)
    if not np.allclose(
        trades.groupby("variant_id")["trade_pnl"].sum().sort_index(),
        daily_pnl.groupby("variant_id")["net_pnl"].sum().sort_index(),
    ):
        raise RuntimeError("RUN-0004 aggregate reconciliation failed")
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
        Path(__file__).with_name("run0003.py"),
        Path(__file__).with_name("run0001.py"),
        Path(__file__).with_name("cam0007.py"),
    ]
    reconciliation = {
        "status": "passed",
        "run_id": "RUN-0004",
        "expected_variant_count": 540,
        "executed_variant_count": int(len(metrics)),
        "resolved_horizons": list(HORIZONS),
        "resolved_entry_minutes": list(ENTRY_MINUTES),
        "resolved_costs_bps_per_side": list(COSTS),
        "resolved_shared_caps": list(SHARED_CAPS),
        "resolved_reserved_caps": list(RESERVED_CAPS),
        "resolved_allocation_rules": list(SHARED_RULES) + ["reserved_half"],
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
    columns = [
        "variant_id",
        "allocation_rule",
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
        "top_20_at_10bp": primary[columns]
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
