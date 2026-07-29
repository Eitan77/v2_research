from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from cam0007 import (
    CUTOFF,
    marketable_long_return,
    max_drawdown_and_recovery,
    protected_short_return,
)


PORTFOLIOS_INTRADAY = {
    "long_positive_continuation": {"long_positive_continuation"},
    "long_negative_failure": {"long_negative_failure"},
    "protected_short_negative_continuation": {
        "protected_short_negative_continuation"
    },
    "protected_short_positive_failure": {
        "protected_short_positive_failure"
    },
    "all_longs": {"long_positive_continuation", "long_negative_failure"},
    "all_shorts": {
        "protected_short_negative_continuation",
        "protected_short_positive_failure",
    },
    "directional_continuation": {
        "long_positive_continuation",
        "protected_short_negative_continuation",
    },
    "failed_reaction": {
        "long_negative_failure",
        "protected_short_positive_failure",
    },
    "all_legs": {
        "long_positive_continuation",
        "long_negative_failure",
        "protected_short_negative_continuation",
        "protected_short_positive_failure",
    },
}
PORTFOLIOS_LONG = {
    "long_positive_continuation": {"long_positive_continuation"},
    "long_negative_failure": {"long_negative_failure"},
    "all_longs": {"long_positive_continuation", "long_negative_failure"},
}
HORIZONS = {
    "next_open": ("next_open_session", "exit_next_open_split", "open"),
    "three_close": ("three_close_session", "exit_three_close_split", "close"),
    "five_close": ("five_close_session", "exit_five_close_split", "close"),
    "ten_close": ("ten_close_session", "exit_ten_close_split", "close"),
}
COSTS = (5, 10, 20)
POSITION_CAP = 0.20
WINDOWS = {
    "full": pd.Timestamp("2024-07-01"),
    "recent_18m": pd.Timestamp("2024-11-01"),
    "recent_15m": pd.Timestamp("2025-02-01"),
    "recent_12m": pd.Timestamp("2025-05-01"),
}
BLOCKS = {
    "block_1_2024h2": (pd.Timestamp("2024-07-01"), pd.Timestamp("2024-12-31")),
    "block_2_2025h1": (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-06-30")),
    "block_3_2025h2": (pd.Timestamp("2025-07-01"), pd.Timestamp("2025-12-31")),
    "block_4_2026ytd": (pd.Timestamp("2026-01-01"), CUTOFF),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify(row: pd.Series) -> str | None:
    gap = float(row["gap_return"])
    reaction = float(row["first30_return"])
    if abs(gap) < 0.02 or reaction == 0:
        return None
    if gap > 0 and reaction > 0:
        return "long_positive_continuation"
    if gap < 0 and reaction > 0:
        return "long_negative_failure"
    if gap < 0 and reaction < 0:
        return "protected_short_negative_continuation"
    if gap > 0 and reaction < 0:
        return "protected_short_positive_failure"
    return None


def allocate_same_day(trades: pd.DataFrame) -> pd.DataFrame:
    result = trades.copy()
    result["position_fraction"] = 0.0
    for _, index in result.groupby("entry_session").groups.items():
        size = min(POSITION_CAP, 1.0 / len(index))
        result.loc[index, "position_fraction"] = size
    return result


def allocate_overlapping(
    trades: pd.DataFrame,
    session_number: dict[pd.Timestamp, int],
    position_cap: float = POSITION_CAP,
) -> pd.DataFrame:
    result = trades.sort_values(
        ["entry_session", "symbol", "event_timestamp"]
    ).copy()
    result["position_fraction"] = 0.0
    active: list[tuple[int, float]] = []
    for entry_session, index in result.groupby("entry_session", sort=True).groups.items():
        entry_order = session_number[pd.Timestamp(entry_session)] * 2 + 1
        active = [(end, size) for end, size in active if end > entry_order]
        available = max(0.0, 1.0 - sum(size for _, size in active))
        size = min(position_cap, available / len(index)) if available > 0 else 0.0
        result.loc[index, "position_fraction"] = size
        for row_index in index:
            row = result.loc[row_index]
            exit_order = session_number[pd.Timestamp(row["exit_session"])] * 2
            if row["exit_timing"] == "close":
                exit_order += 2
            if size > 0:
                active.append((exit_order, size))
    return result


def monthly_grid(daily: pd.DataFrame, start: pd.Timestamp) -> pd.Series:
    periods = pd.period_range(start=start, end=CUTOFF, freq="M")
    values = (
        daily[daily["date"].ge(start)]
        .assign(month=lambda x: x["date"].dt.to_period("M"))
        .groupby("month")["net_pnl"]
        .sum()
    )
    return values.reindex(periods, fill_value=0.0)


def build_metrics(
    variant_id: str,
    portfolio: str,
    horizon: str,
    cost: int,
    trades: pd.DataFrame,
    daily: pd.DataFrame,
) -> dict:
    allocated = trades[trades["position_fraction"].gt(0)].copy()
    if len(allocated) and "exit_timing" in allocated.columns:
        exposure_changes = []
        for trade in allocated.itertuples(index=False):
            entry_clock = pd.Timestamp(trade.entry_session) + pd.Timedelta(hours=10)
            exit_hour = 9.5 if trade.exit_timing == "open" else 16.0
            exit_clock = pd.Timestamp(trade.exit_session) + pd.Timedelta(hours=exit_hour)
            exposure_changes.extend(
                [
                    (entry_clock, 1, float(trade.position_fraction)),
                    (exit_clock, 0, -float(trade.position_fraction)),
                ]
            )
        gross = 0.0
        peak_gross = 0.0
        for _, _, change in sorted(exposure_changes):
            gross += change
            peak_gross = max(peak_gross, gross)
    elif len(allocated):
        peak_gross = float(
            allocated.groupby("entry_session")["position_fraction"].sum().max()
        )
    else:
        peak_gross = 0.0
    row: dict = {
        "variant_id": variant_id,
        "portfolio": portfolio,
        "horizon": horizon,
        "cost_bps_per_side": cost,
        "candidate_events": int(len(trades)),
        "allocated_trades": int(len(allocated)),
        "symbols": int(allocated["symbol"].nunique()) if len(allocated) else 0,
        "total_net_return": float(daily["net_pnl"].sum()),
        "maximum_gross_exposure": peak_gross,
        "stop_count": int(allocated["stopped"].sum()) if len(allocated) else 0,
        "stop_rate": float(allocated["stopped"].mean()) if len(allocated) else 0.0,
    }
    maximum_drawdown, recovery_days, unresolved = max_drawdown_and_recovery(daily)
    row.update(
        {
            "maximum_drawdown": maximum_drawdown,
            "recovery_days": recovery_days,
            "recovery_unresolved": unresolved,
        }
    )
    for label, start in WINDOWS.items():
        months = monthly_grid(daily, start)
        row[f"{label}_net_return"] = float(months.sum())
        row[f"{label}_average_month"] = float(months.mean())
        row[f"{label}_positive_months"] = int(months.gt(0).sum())
        row[f"{label}_negative_months"] = int(months.lt(0).sum())
        row[f"{label}_inactive_months"] = int(months.eq(0).sum())
    for label, (start, end) in BLOCKS.items():
        row[f"{label}_net_return"] = float(
            daily.loc[daily["date"].between(start, end), "net_pnl"].sum()
        )
    if allocated.empty:
        row.update(
            {
                "positive_trade_pnl": 0.0,
                "top5_event_positive_share": np.nan,
                "top5_day_positive_share": np.nan,
                "top_symbol_positive_share": np.nan,
                "top_symbol": None,
                "top_symbol_net_pnl": 0.0,
                "after_close_net_pnl": 0.0,
                "premarket_net_pnl": 0.0,
            }
        )
        return row
    positive = allocated["trade_pnl"].clip(lower=0)
    denominator = float(positive.sum())
    by_day = allocated.groupby("entry_session")["trade_pnl"].sum()
    by_symbol = allocated.groupby("symbol")["trade_pnl"].sum().sort_values(
        ascending=False
    )
    by_bucket = allocated.groupby("announcement_bucket")["trade_pnl"].sum()
    row.update(
        {
            "positive_trade_pnl": denominator,
            "top5_event_positive_share": float(positive.nlargest(5).sum() / denominator)
            if denominator > 0
            else np.nan,
            "top5_day_positive_share": float(
                by_day.clip(lower=0).nlargest(5).sum() / denominator
            )
            if denominator > 0
            else np.nan,
            "top_symbol_positive_share": float(
                max(0.0, float(by_symbol.iloc[0])) / denominator
            )
            if denominator > 0
            else np.nan,
            "top_symbol": str(by_symbol.index[0]),
            "top_symbol_net_pnl": float(by_symbol.iloc[0]),
            "after_close_net_pnl": float(by_bucket.get("after_close", 0.0)),
            "premarket_net_pnl": float(by_bucket.get("premarket", 0.0)),
        }
    )
    return row


def same_day_variant(
    eligible: pd.DataFrame,
    portfolio: str,
    legs: set[str],
    cost: int,
    sessions: pd.DatetimeIndex,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected = eligible[
        eligible["leg"].isin(legs) & eligible["same_day_complete"]
    ].copy()
    records = []
    for row in selected.itertuples(index=False):
        stopped = False
        if row.leg.startswith("protected_short"):
            unit_return, stopped, effective_exit = protected_short_return(
                float(row.entry_1000_raw),
                float(row.exit_final_split / row.split_factor),
                [float(row.path_high_1000_to_final_raw)],
                0.02,
                cost,
                10,
            )
        else:
            unit_return = marketable_long_return(
                float(row.entry_1000_split), float(row.exit_final_split), cost
            )
            effective_exit = float(row.exit_final_split)
        records.append(
            {
                "event_id": f"{row.symbol}|{row.event_timestamp.isoformat()}",
                "symbol": row.symbol,
                "event_timestamp": row.event_timestamp,
                "entry_session": pd.Timestamp(row.entry_session),
                "exit_session": pd.Timestamp(row.entry_session),
                "announcement_bucket": row.announcement_bucket,
                "leg": row.leg,
                "gap_return": row.gap_return,
                "first30_return": row.first30_return,
                "unit_net_return": unit_return,
                "effective_exit": effective_exit,
                "stopped": stopped,
            }
        )
    trades = allocate_same_day(pd.DataFrame(records)) if records else pd.DataFrame(
        columns=[
            "event_id",
            "symbol",
            "event_timestamp",
            "entry_session",
            "exit_session",
            "announcement_bucket",
            "leg",
            "gap_return",
            "first30_return",
            "unit_net_return",
            "effective_exit",
            "stopped",
            "position_fraction",
        ]
    )
    trades["trade_pnl"] = trades["unit_net_return"] * trades["position_fraction"]
    pnl = trades.groupby("exit_session")["trade_pnl"].sum()
    daily = pd.DataFrame({"date": sessions})
    daily["net_pnl"] = daily["date"].map(pnl).fillna(0.0)
    return trades, daily


def multiday_variant(
    eligible: pd.DataFrame,
    daily_prices: pd.DataFrame,
    portfolio: str,
    legs: set[str],
    horizon: str,
    cost: int,
    sessions: pd.DatetimeIndex,
    session_number: dict[pd.Timestamp, int],
    position_cap: float = POSITION_CAP,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    session_column, exit_column, exit_timing = HORIZONS[horizon]
    selected = eligible[
        eligible["leg"].isin(legs)
        & eligible[session_column].notna()
        & eligible[exit_column].notna()
    ].copy()
    selected["exit_session"] = pd.to_datetime(selected[session_column])
    selected["exit_price"] = selected[exit_column].astype(float)
    selected["exit_timing"] = exit_timing
    selected["event_id"] = (
        selected["symbol"]
        + "|"
        + selected["event_timestamp"].astype(str)
    )
    selected["unit_net_return"] = (
        selected["exit_price"] / selected["entry_1000_split"]
        - 1.0
        - 2.0 * cost / 10_000.0
    )
    selected["stopped"] = False
    trades = allocate_overlapping(selected, session_number, position_cap)
    trades["trade_pnl"] = (
        trades["unit_net_return"] * trades["position_fraction"]
    )

    close_lookup = daily_prices.set_index(["symbol", "date"])["close"]
    pnl: defaultdict[pd.Timestamp, float] = defaultdict(float)
    for row in trades[trades["position_fraction"].gt(0)].itertuples(index=False):
        size = float(row.position_fraction)
        entry = float(row.entry_1000_split)
        entry_date = pd.Timestamp(row.entry_session)
        exit_date = pd.Timestamp(row.exit_session)
        entry_index = session_number[entry_date]
        exit_index = session_number[exit_date]
        entry_close = float(close_lookup.loc[(row.symbol, entry_date)])
        pnl[entry_date] += size * (
            (entry_close - entry) / entry - cost / 10_000.0
        )
        previous_close = entry_close
        if row.exit_timing == "open":
            pnl[exit_date] += size * (
                (float(row.exit_price) - previous_close) / entry
                - cost / 10_000.0
            )
            continue
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
        raise RuntimeError(f"Marked-to-market reconciliation failed: {portfolio}/{horizon}/{cost}")
    return trades, daily


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record", type=Path, required=True)
    parser.add_argument("--event-readiness", type=Path, required=True)
    parser.add_argument("--daily-split", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    record = yaml.safe_load(args.run_record.read_text(encoding="utf-8"))
    if record["status"] != "frozen":
        raise RuntimeError("RUN-0001 record is not frozen")
    if record["frozen_configuration"]["expected_variant_count"]["total"] != 63:
        raise RuntimeError("Frozen expected variant count mismatch")

    readiness = pd.read_parquet(args.event_readiness)
    daily_prices = pd.read_parquet(args.daily_split)
    readiness["entry_session"] = pd.to_datetime(readiness["entry_session"])
    daily_prices["date"] = pd.to_datetime(daily_prices["date"])
    if readiness["entry_session"].max() > CUTOFF or daily_prices["date"].max() > CUTOFF:
        raise RuntimeError("RUN-0001 input crosses sealed boundary")
    sessions = pd.DatetimeIndex(
        sorted(daily_prices["date"].drop_duplicates())
    )
    session_number = {pd.Timestamp(date): index for index, date in enumerate(sessions)}
    eligible = readiness[
        readiness["signal_complete"]
        & readiness["prior20_median_dollar_volume"].ge(100_000_000)
    ].copy()
    eligible["leg"] = eligible.apply(classify, axis=1)
    eligible = eligible[eligible["leg"].notna()].copy()

    metric_rows = []
    trade_frames = []
    daily_frames = []
    for portfolio, legs in PORTFOLIOS_INTRADAY.items():
        for cost in COSTS:
            variant_id = f"intraday__{portfolio}__{cost}bp"
            trades, daily = same_day_variant(
                eligible, portfolio, legs, cost, sessions
            )
            trades["variant_id"] = variant_id
            daily["variant_id"] = variant_id
            metric_rows.append(
                build_metrics(
                    variant_id, portfolio, "intraday", cost, trades, daily
                )
            )
            trade_frames.append(trades)
            daily_frames.append(daily)
    for portfolio, legs in PORTFOLIOS_LONG.items():
        for horizon in HORIZONS:
            for cost in COSTS:
                variant_id = f"{horizon}__{portfolio}__{cost}bp"
                trades, daily = multiday_variant(
                    eligible,
                    daily_prices,
                    portfolio,
                    legs,
                    horizon,
                    cost,
                    sessions,
                    session_number,
                )
                trades["variant_id"] = variant_id
                daily["variant_id"] = variant_id
                metric_rows.append(
                    build_metrics(
                        variant_id, portfolio, horizon, cost, trades, daily
                    )
                )
                trade_frames.append(trades)
                daily_frames.append(daily)
    metrics = pd.DataFrame(metric_rows)
    if len(metrics) != 63 or metrics["variant_id"].nunique() != 63:
        raise RuntimeError(f"Executed {len(metrics)} variants, expected 63")
    trades = pd.concat(trade_frames, ignore_index=True)
    daily = pd.concat(daily_frames, ignore_index=True)

    attrition = {
        "registry_events": int(len(readiness)),
        "signal_complete_events": int(readiness["signal_complete"].sum()),
        "liquidity_eligible_signal_events": int(
            (
                readiness["signal_complete"]
                & readiness["prior20_median_dollar_volume"].ge(100_000_000)
            ).sum()
        ),
        "absolute_gap_and_nonzero_reaction_events": int(len(eligible)),
        "leg_counts": {
            str(key): int(value)
            for key, value in eligible["leg"].value_counts().items()
        },
        "same_day_complete_eligible_events": int(
            eligible["same_day_complete"].sum()
        ),
        "horizon_complete_eligible_events": {
            horizon: int(
                eligible[session_column].notna().sum()
                & eligible[exit_column].notna().sum()
            )
            for horizon, (session_column, exit_column, _) in HORIZONS.items()
        },
        "maximum_loaded_date": str(
            max(readiness["entry_session"].max(), daily_prices["date"].max()).date()
        ),
        "holdout_rows_loaded": 0,
    }
    # Correct a boolean-count expression explicitly for transparent attrition.
    attrition["horizon_complete_eligible_events"] = {
        horizon: int(
            (
                eligible[session_column].notna()
                & eligible[exit_column].notna()
            ).sum()
        )
        for horizon, (session_column, exit_column, _) in HORIZONS.items()
    }
    metrics = metrics.sort_values(
        ["recent_15m_average_month", "maximum_drawdown"],
        ascending=[False, True],
    ).reset_index(drop=True)
    metrics.to_parquet(args.output_dir / "variant_metrics.parquet", index=False)
    trades.to_parquet(args.output_dir / "trade_details.parquet", index=False)
    daily.to_parquet(args.output_dir / "daily_pnl.parquet", index=False)
    (args.output_dir / "attrition.json").write_text(
        json.dumps(attrition, indent=2) + "\n", encoding="utf-8"
    )
    artifacts = {
        "variant_metrics.parquet": sha256(args.output_dir / "variant_metrics.parquet"),
        "trade_details.parquet": sha256(args.output_dir / "trade_details.parquet"),
        "daily_pnl.parquet": sha256(args.output_dir / "daily_pnl.parquet"),
        "attrition.json": sha256(args.output_dir / "attrition.json"),
    }
    reconciliation = {
        "status": "passed",
        "run_id": record["run_id"],
        "run_record_status": record["status"],
        "resolved_costs_bps_per_side": list(COSTS),
        "resolved_position_cap": POSITION_CAP,
        "resolved_intraday_portfolios": list(PORTFOLIOS_INTRADAY),
        "resolved_multiday_long_portfolios": list(PORTFOLIOS_LONG),
        "resolved_horizons": list(HORIZONS),
        "expected_variant_count": 63,
        "executed_variant_count": int(len(metrics)),
        "input_hashes": {
            "run_record": sha256(args.run_record),
            "event_readiness": sha256(args.event_readiness),
            "daily_split": sha256(args.daily_split),
        },
        "output_hashes": artifacts,
        "maximum_loaded_date": attrition["maximum_loaded_date"],
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2) + "\n", encoding="utf-8"
    )
    summary_columns = [
        "variant_id",
        "allocated_trades",
        "symbols",
        "recent_15m_average_month",
        "recent_15m_net_return",
        "recent_15m_positive_months",
        "recent_15m_negative_months",
        "recent_15m_inactive_months",
        "maximum_drawdown",
        "recovery_days",
        "total_net_return",
        "top5_event_positive_share",
        "top_symbol",
        "stop_rate",
    ]
    summary = {
        "status": "completed_uninterpreted",
        "variant_count": int(len(metrics)),
        "top_10_by_frozen_sort": metrics[summary_columns]
        .head(10)
        .replace({np.nan: None})
        .to_dict(orient="records"),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
