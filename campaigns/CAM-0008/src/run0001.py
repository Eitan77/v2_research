from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from cam0008 import (
    CUTOFF,
    max_drawdown_and_recovery,
    protected_short_return,
)


COSTS = (5, 10, 20)
SCOPES = ("all_events", "standalone_analyst")
INTRADAY_SELECTORS = {
    "positive_continuation_long": {"positive_continuation_long"},
    "negative_failure_long": {"negative_failure_long"},
    "negative_continuation_short": {"negative_continuation_short"},
    "positive_failure_short": {"positive_failure_short"},
    "all_longs": {"positive_continuation_long", "negative_failure_long"},
    "all_shorts": {
        "negative_continuation_short",
        "positive_failure_short",
    },
    "aligned_action": {
        "positive_continuation_long",
        "negative_continuation_short",
    },
    "failed_action": {"negative_failure_long", "positive_failure_short"},
    "all_legs": {
        "positive_continuation_long",
        "negative_failure_long",
        "negative_continuation_short",
        "positive_failure_short",
    },
}
MULTIDAY_SELECTORS = (
    "positive_continuation_long",
    "negative_failure_long",
    "all_longs",
    "positive_action_all_long",
)
HORIZONS = {
    "next_open": ("next_open_session", "exit_next_open_split", "open"),
    "two_close": ("two_close_session", "exit_two_close_split", "close"),
    "three_close": ("three_close_session", "exit_three_close_split", "close"),
    "five_close": ("five_close_session", "exit_five_close_split", "close"),
    "ten_close": ("ten_close_session", "exit_ten_close_split", "close"),
}
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
    reaction = float(row["reaction_return"])
    sign = int(row["action_sign"])
    if reaction == 0:
        return None
    if sign > 0 and reaction > 0:
        return "positive_continuation_long"
    if sign < 0 and reaction > 0:
        return "negative_failure_long"
    if sign < 0 and reaction < 0:
        return "negative_continuation_short"
    if sign > 0 and reaction < 0:
        return "positive_failure_short"
    return None


def clock(date: pd.Timestamp, minute: str) -> pd.Timestamp:
    hour, minute_value = map(int, minute.split(":"))
    return pd.Timestamp(date) + pd.Timedelta(hours=hour, minutes=minute_value)


def allocate(
    candidates: pd.DataFrame,
    position_cap: float,
    maximum_symbol_gross: float = 0.10,
) -> pd.DataFrame:
    result = candidates.sort_values(
        ["entry_timestamp", "symbol", "event_timestamp"]
    ).copy()
    result["position_fraction"] = 0.0
    active: list[tuple[pd.Timestamp, str, float]] = []
    for entry_timestamp, index in result.groupby(
        "entry_timestamp", sort=True
    ).groups.items():
        active = [
            item for item in active if item[0] > pd.Timestamp(entry_timestamp)
        ]
        gross = sum(size for _, _, size in active)
        available = max(0.0, 1.0 - gross)
        if available <= 1e-12:
            available = 0.0
        base_size = (
            min(position_cap, available / len(index))
            if available > 0
            else 0.0
        )
        symbol_gross = defaultdict(float)
        for _, symbol, size in active:
            symbol_gross[symbol] += size
        for row_index in index:
            row = result.loc[row_index]
            size = min(
                base_size,
                max(0.0, maximum_symbol_gross - symbol_gross[row["symbol"]]),
            )
            if size <= 1e-12:
                size = 0.0
            result.loc[row_index, "position_fraction"] = size
            if size > 0:
                active.append(
                    (
                        pd.Timestamp(row["exit_timestamp"]),
                        str(row["symbol"]),
                        float(size),
                    )
                )
                symbol_gross[row["symbol"]] += size
    return result


def prepare_candidates(
    eligible: pd.DataFrame,
    selector: str,
    horizon: str,
    cost: int,
) -> pd.DataFrame:
    if selector == "positive_action_all_long":
        selected = eligible[eligible["action_sign"].eq(1)].copy()
        selected["trade_direction"] = "long"
    else:
        legs = (
            INTRADAY_SELECTORS[selector]
            if selector in INTRADAY_SELECTORS
            else {selector}
        )
        selected = eligible[eligible["leg"].isin(legs)].copy()
        selected["trade_direction"] = np.where(
            selected["leg"].str.endswith("_short"), "short", "long"
        )
    if horizon == "intraday":
        selected = selected[selected["same_day_complete"]].copy()
        selected["exit_session"] = selected["entry_session"]
        selected["exit_price"] = selected["exit_final_split"]
        selected["exit_timing"] = "close"
        selected["exit_timestamp"] = [
            clock(date, minute)
            for date, minute in zip(
                selected["entry_session"], selected["final_exit_minute"]
            )
        ]
    else:
        session_column, exit_column, exit_timing = HORIZONS[horizon]
        selected = selected[
            selected[session_column].notna() & selected[exit_column].notna()
        ].copy()
        selected["exit_session"] = pd.to_datetime(selected[session_column])
        selected["exit_price"] = selected[exit_column]
        selected["exit_timing"] = exit_timing
        exit_clock = "09:30" if exit_timing == "open" else "16:00"
        selected["exit_timestamp"] = [
            clock(date, exit_clock) for date in selected["exit_session"]
        ]
    selected["entry_timestamp"] = [
        clock(date, minute)
        for date, minute in zip(
            selected["entry_session"], selected["entry_minute"]
        )
    ]
    selected["event_id"] = (
        selected["symbol"] + "|" + selected["event_timestamp"].astype(str)
    )
    selected["stopped"] = False
    selected["effective_exit"] = selected["exit_price"]
    unit_returns = []
    stopped_values = []
    effective_exits = []
    for row in selected.itertuples(index=False):
        if row.trade_direction == "short":
            pnl, stopped, effective = protected_short_return(
                float(row.entry_raw),
                float(row.exit_price / row.split_factor),
                [float(row.path_high_entry_to_final_raw)],
                0.02,
                cost,
                10,
            )
            unit_returns.append(pnl)
            stopped_values.append(stopped)
            effective_exits.append(effective * row.split_factor)
        else:
            unit_returns.append(
                float(
                    row.exit_price / row.entry_split
                    - 1
                    - 2 * cost / 10_000
                )
            )
            stopped_values.append(False)
            effective_exits.append(float(row.exit_price))
    selected["unit_net_return"] = unit_returns
    selected["stopped"] = stopped_values
    selected["effective_exit"] = effective_exits
    return selected


def simulate(
    candidates: pd.DataFrame,
    daily_prices: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    cost: int,
    position_cap: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    trades = allocate(candidates, position_cap)
    trades["trade_pnl"] = (
        trades["unit_net_return"] * trades["position_fraction"]
    )
    pnl: defaultdict[pd.Timestamp, float] = defaultdict(float)
    close_lookup = daily_prices.set_index(["symbol", "date"])["close"]
    session_number = {
        pd.Timestamp(date): index for index, date in enumerate(sessions)
    }
    for row in trades[trades["position_fraction"].gt(0)].itertuples(index=False):
        size = float(row.position_fraction)
        if row.exit_session == row.entry_session:
            pnl[pd.Timestamp(row.exit_session)] += float(row.trade_pnl)
            continue
        entry = float(row.entry_split)
        entry_date = pd.Timestamp(row.entry_session)
        exit_date = pd.Timestamp(row.exit_session)
        entry_close = float(close_lookup.loc[(row.symbol, entry_date)])
        pnl[entry_date] += size * (
            (entry_close - entry) / entry - cost / 10_000
        )
        previous = entry_close
        if row.exit_timing == "open":
            pnl[exit_date] += size * (
                (float(row.exit_price) - previous) / entry - cost / 10_000
            )
            continue
        for index in range(
            session_number[entry_date] + 1, session_number[exit_date] + 1
        ):
            date = pd.Timestamp(sessions[index])
            current = float(close_lookup.loc[(row.symbol, date)])
            increment = (current - previous) / entry
            if date == exit_date:
                increment -= cost / 10_000
            pnl[date] += size * increment
            previous = current
    daily = pd.DataFrame({"date": sessions})
    daily["net_pnl"] = daily["date"].map(pnl).fillna(0.0)
    if not np.isclose(daily["net_pnl"].sum(), trades["trade_pnl"].sum()):
        raise RuntimeError("Marked-to-market P&L reconciliation failed")
    return trades, daily


def month_grid(daily: pd.DataFrame, start: pd.Timestamp) -> pd.Series:
    periods = pd.period_range(start=start, end=CUTOFF, freq="M")
    values = (
        daily[daily["date"].ge(start)]
        .assign(month=lambda x: x["date"].dt.to_period("M"))
        .groupby("month")["net_pnl"]
        .sum()
    )
    return values.reindex(periods, fill_value=0.0)


def metrics(
    variant_id: str,
    scope: str,
    selector: str,
    horizon: str,
    cost: int,
    trades: pd.DataFrame,
    daily: pd.DataFrame,
) -> dict:
    allocated = trades[trades["position_fraction"].gt(0)].copy()
    row = {
        "variant_id": variant_id,
        "scope": scope,
        "selector": selector,
        "horizon": horizon,
        "cost_bps_per_side": cost,
        "candidate_events": int(len(trades)),
        "allocated_trades": int(len(allocated)),
        "symbols": int(allocated["symbol"].nunique()) if len(allocated) else 0,
        "total_net_return": float(daily["net_pnl"].sum()),
        "stop_count": int(allocated["stopped"].sum()) if len(allocated) else 0,
        "stop_rate": float(allocated["stopped"].mean()) if len(allocated) else 0.0,
    }
    drawdown, recovery, unresolved = max_drawdown_and_recovery(daily)
    row.update(
        {
            "maximum_drawdown": drawdown,
            "recovery_days": recovery,
            "recovery_unresolved": unresolved,
        }
    )
    for label, start in WINDOWS.items():
        months = month_grid(daily, start)
        row[f"{label}_average_month"] = float(months.mean())
        row[f"{label}_net_return"] = float(months.sum())
        row[f"{label}_positive_months"] = int(months.gt(0).sum())
        row[f"{label}_negative_months"] = int(months.lt(0).sum())
        row[f"{label}_inactive_months"] = int(months.eq(0).sum())
    for label, (start, end) in BLOCKS.items():
        row[f"{label}_net_return"] = float(
            daily.loc[daily["date"].between(start, end), "net_pnl"].sum()
        )
    if allocated.empty:
        return row
    positive = allocated["trade_pnl"].clip(lower=0)
    denominator = float(positive.sum())
    by_day = allocated.groupby("entry_session")["trade_pnl"].sum()
    by_symbol = allocated.groupby("symbol")["trade_pnl"].sum().sort_values(
        ascending=False
    )
    allocated["primary_firm"] = allocated["firms"].map(
        lambda value: json.loads(value)[0] if json.loads(value) else "unknown"
    )
    by_firm = allocated.groupby("primary_firm")["trade_pnl"].sum().sort_values(
        ascending=False
    )
    row.update(
        {
            "top5_event_positive_share": float(
                positive.nlargest(5).sum() / denominator
            )
            if denominator > 0
            else np.nan,
            "top5_day_positive_share": float(
                by_day.clip(lower=0).nlargest(5).sum() / denominator
            )
            if denominator > 0
            else np.nan,
            "top_symbol": str(by_symbol.index[0]),
            "top_symbol_net_pnl": float(by_symbol.iloc[0]),
            "top_firm": str(by_firm.index[0]),
            "top_firm_net_pnl": float(by_firm.iloc[0]),
        }
    )
    return row


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
    if record["frozen_configuration"]["expected_variant_count"]["total"] != 174:
        raise RuntimeError("Frozen variant count mismatch")
    readiness = pd.read_parquet(args.event_readiness)
    daily_prices = pd.read_parquet(args.daily_split)
    readiness["entry_session"] = pd.to_datetime(readiness["entry_session"])
    daily_prices["date"] = pd.to_datetime(daily_prices["date"])
    if max(readiness["entry_session"].max(), daily_prices["date"].max()) > CUTOFF:
        raise RuntimeError("RUN-0001 input crosses sealed boundary")
    eligible = readiness[
        readiness["signal_complete"]
        & readiness["prior20_median_dollar_volume"].ge(100_000_000)
    ].copy()
    eligible["leg"] = eligible.apply(classify, axis=1)
    sessions = pd.DatetimeIndex(sorted(daily_prices["date"].drop_duplicates()))
    metric_rows = []
    trade_frames = []
    daily_frames = []
    for scope in SCOPES:
        scoped = (
            eligible
            if scope == "all_events"
            else eligible[~eligible["within_36h_after_earnings"]]
        )
        for selector in INTRADAY_SELECTORS:
            for cost in COSTS:
                variant_id = f"{scope}__{selector}__intraday__{cost}bp"
                candidates = prepare_candidates(
                    scoped, selector, "intraday", cost
                )
                trades, daily = simulate(
                    candidates, daily_prices, sessions, cost, 0.10
                )
                trades["variant_id"] = variant_id
                daily["variant_id"] = variant_id
                metric_rows.append(
                    metrics(
                        variant_id,
                        scope,
                        selector,
                        "intraday",
                        cost,
                        trades,
                        daily,
                    )
                )
                trade_frames.append(trades)
                daily_frames.append(daily)
        for selector in MULTIDAY_SELECTORS:
            for horizon in HORIZONS:
                for cost in COSTS:
                    variant_id = f"{scope}__{selector}__{horizon}__{cost}bp"
                    candidates = prepare_candidates(
                        scoped, selector, horizon, cost
                    )
                    trades, daily = simulate(
                        candidates, daily_prices, sessions, cost, 0.02
                    )
                    trades["variant_id"] = variant_id
                    daily["variant_id"] = variant_id
                    metric_rows.append(
                        metrics(
                            variant_id,
                            scope,
                            selector,
                            horizon,
                            cost,
                            trades,
                            daily,
                        )
                    )
                    trade_frames.append(trades)
                    daily_frames.append(daily)
    metric_frame = pd.DataFrame(metric_rows)
    if len(metric_frame) != 174 or metric_frame["variant_id"].nunique() != 174:
        raise RuntimeError(f"Executed {len(metric_frame)} variants, expected 174")
    trades = pd.concat(trade_frames, ignore_index=True)
    daily = pd.concat(daily_frames, ignore_index=True)
    if not np.allclose(
        trades.groupby("variant_id")["trade_pnl"].sum().sort_index(),
        daily.groupby("variant_id")["net_pnl"].sum().sort_index(),
    ):
        raise RuntimeError("Aggregate variant reconciliation failed")
    metric_frame = metric_frame.sort_values(
        ["recent_15m_average_month", "maximum_drawdown"],
        ascending=[False, True],
    ).reset_index(drop=True)
    metric_frame.to_parquet(
        args.output_dir / "variant_metrics.parquet", index=False
    )
    trades.to_parquet(args.output_dir / "trade_details.parquet", index=False)
    daily.to_parquet(args.output_dir / "daily_pnl.parquet", index=False)
    attrition = {
        "registry_events": int(len(readiness)),
        "signal_complete_events": int(readiness["signal_complete"].sum()),
        "liquidity_eligible_signal_events": int(len(eligible)),
        "classified_leg_counts": {
            str(key): int(value)
            for key, value in eligible["leg"].value_counts(dropna=False).items()
        },
        "standalone_events": int(
            (~eligible["within_36h_after_earnings"]).sum()
        ),
        "earnings_confound_events": int(
            eligible["within_36h_after_earnings"].sum()
        ),
        "maximum_loaded_date": "2026-04-30",
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
    reconciliation = {
        "status": "passed",
        "run_id": "RUN-0001",
        "expected_variant_count": 174,
        "executed_variant_count": int(len(metric_frame)),
        "resolved_scopes": list(SCOPES),
        "resolved_intraday_selectors": list(INTRADAY_SELECTORS),
        "resolved_multiday_selectors": list(MULTIDAY_SELECTORS),
        "resolved_horizons": list(HORIZONS),
        "resolved_costs": list(COSTS),
        "frozen_configuration_hash": frozen_hash,
        "input_hashes": {
            "event_readiness": sha256(args.event_readiness),
            "daily_split": sha256(args.daily_split),
        },
        "executed_code_hashes": {
            Path(__file__).name: sha256(Path(__file__)),
            "cam0008.py": sha256(Path(__file__).with_name("cam0008.py")),
        },
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2) + "\n", encoding="utf-8"
    )
    columns = [
        "variant_id",
        "scope",
        "selector",
        "horizon",
        "cost_bps_per_side",
        "candidate_events",
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
        "top_firm",
        "stop_rate",
    ]
    summary = {
        "status": "completed_uninterpreted",
        "variant_count": int(len(metric_frame)),
        "top_20": metric_frame[columns]
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
