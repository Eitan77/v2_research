from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cam0006 import (
    CUTOFF,
    allocate_daily,
    marketable_long_return,
    max_drawdown_and_recovery,
    protected_short_return,
)


LIQUIDITY_FLOOR = 100_000_000.0
TAIL = 0.10
MIN_GAP = 0.005
DIRECTIONS = ("continuation", "absorption")
HORIZONS = ("0945", "1000", "1200", "final")
PORTFOLIOS = ("long_only", "short_only", "balanced")
COSTS = (0, 5, 10, 20)
WINDOW_STARTS = {
    "18m": pd.Timestamp("2024-11-01"),
    "15m": pd.Timestamp("2025-02-01"),
    "12m": pd.Timestamp("2025-05-01"),
}


def aggregate_marks(events: pd.DataFrame, minute_path: Path) -> pd.DataFrame:
    connection = duckdb.connect()
    connection.register(
        "events",
        events[
            [
                "symbol",
                "date",
                "liquidation_minute",
            ]
        ],
    )
    path = minute_path.as_posix()
    return connection.execute(
        f"""
        SELECT e.symbol, e.date,
          MAX(m.close) FILTER (WHERE m.minute='09:30') AS first_close,
          MAX(m.open) FILTER (WHERE m.minute='09:31') AS entry_open,
          MAX(m.open) FILTER (WHERE m.minute='09:45') AS exit_0945,
          MAX(m.open) FILTER (WHERE m.minute='10:00') AS exit_1000,
          MAX(m.open) FILTER (WHERE m.minute='12:00') AS exit_1200,
          MAX(m.open) FILTER (
            WHERE m.minute=e.liquidation_minute
          ) AS exit_final,
          MAX(m.high) FILTER (
            WHERE m.minute>='09:31' AND m.minute<'09:45'
          ) AS high_0945,
          MAX(m.high) FILTER (
            WHERE m.minute>='09:31' AND m.minute<'10:00'
          ) AS high_1000,
          MAX(m.high) FILTER (
            WHERE m.minute>='09:31' AND m.minute<'12:00'
          ) AS high_1200,
          MAX(m.high) FILTER (
            WHERE m.minute>='09:31' AND m.minute<e.liquidation_minute
          ) AS high_final
        FROM events e
        LEFT JOIN READ_PARQUET('{path}') m
          ON e.symbol=m.symbol AND e.date=m.date
        GROUP BY e.symbol, e.date
        ORDER BY e.date, e.symbol
        """
    ).fetchdf()


def prepare_signals(events: pd.DataFrame, marks: pd.DataFrame) -> pd.DataFrame:
    frame = events[
        events["signal_complete"]
        & events["prior_dollar_volume"].ge(LIQUIDITY_FLOOR)
    ].copy()
    frame = frame.merge(marks, on=["symbol", "date"], how="left", validate="one_to_one")
    frame["first_minute_return"] = (
        frame["first_close"] / frame["auction_price_raw"] - 1.0
    )
    frame["gap_rank"] = frame.groupby("date")["raw_gap"].rank(
        pct=True, method="average"
    )
    frame["negative_tail"] = (
        frame["gap_rank"].le(TAIL) & frame["raw_gap"].le(-MIN_GAP)
    )
    frame["positive_tail"] = (
        frame["gap_rank"].ge(1.0 - TAIL) & frame["raw_gap"].ge(MIN_GAP)
    )
    same_sign = np.sign(frame["first_minute_return"]).eq(np.sign(frame["raw_gap"]))
    frame["continuation_signal"] = (
        (frame["negative_tail"] | frame["positive_tail"])
        & same_sign
        & frame["first_minute_return"].ne(0)
    )
    frame["absorption_signal"] = (
        (frame["negative_tail"] | frame["positive_tail"])
        & ~same_sign
        & frame["first_minute_return"].ne(0)
    )
    frame["continuation_leg"] = np.where(
        frame["positive_tail"], "long", "short"
    )
    frame["absorption_leg"] = np.where(
        frame["negative_tail"], "long", "short"
    )
    return frame


def build_results(
    signals: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    decision_rows: list[dict] = []
    position_rows: list[dict] = []
    summary_rows: list[dict] = []
    monthly_rows: list[dict] = []
    months = pd.period_range("2024-11", "2026-04", freq="M")

    for direction in DIRECTIONS:
        signal_column = f"{direction}_signal"
        leg_column = f"{direction}_leg"
        candidates = signals[signals[signal_column]].copy()
        for horizon in HORIZONS:
            exit_column = f"exit_{horizon}"
            high_column = f"high_{horizon}"
            for cost in COSTS:
                daily_leg_returns: dict[pd.Timestamp, dict[str, list[float]]] = {}
                daily_valid: dict[pd.Timestamp, bool] = {}
                for date, group in candidates.groupby("date"):
                    date = pd.Timestamp(date)
                    valid = (
                        group["entry_open"].gt(0)
                        & group[exit_column].gt(0)
                        & group[high_column].gt(0)
                    ).all()
                    daily_valid[date] = bool(valid)
                    daily_leg_returns[date] = {"long": [], "short": []}
                    if not valid:
                        continue
                    for item in group.itertuples():
                        leg = getattr(item, leg_column)
                        entry = float(item.entry_open)
                        exit_price = float(getattr(item, exit_column))
                        if leg == "long":
                            pnl = marketable_long_return(entry, exit_price, cost)
                            stopped = False
                            modeled_exit = exit_price
                        else:
                            pnl, stopped, modeled_exit = protected_short_return(
                                entry,
                                exit_price,
                                [float(getattr(item, high_column))],
                                0.02,
                                cost,
                                10,
                            )
                        daily_leg_returns[date][leg].append(pnl)
                        position_rows.append(
                            {
                                "date": date,
                                "symbol": item.symbol,
                                "direction": direction,
                                "horizon": horizon,
                                "cost_bps_per_side": cost,
                                "leg": leg,
                                "entry": entry,
                                "planned_exit": exit_price,
                                "modeled_exit": modeled_exit,
                                "stopped": stopped,
                                "net_return": pnl,
                                "raw_gap": item.raw_gap,
                                "first_minute_return": item.first_minute_return,
                                "auction_size": item.auction_size,
                                "auction_dollar_value": item.auction_dollar_value,
                                "prior_dollar_volume": item.prior_dollar_volume,
                                "complete_trade_minute_path": item.complete_path,
                            }
                        )
                for portfolio in PORTFOLIOS:
                    variant = f"{direction}_{horizon}_{portfolio}_c{cost}"
                    decisions = []
                    invalid_days = 0
                    for date in sorted(daily_leg_returns):
                        valid = daily_valid[date]
                        if not valid:
                            invalid_days += 1
                            pnl = 0.0
                            long_count = 0
                            short_count = 0
                        else:
                            long_returns = daily_leg_returns[date]["long"]
                            short_returns = daily_leg_returns[date]["short"]
                            pnl = allocate_daily(
                                long_returns, short_returns, portfolio
                            )
                            long_count = len(long_returns)
                            short_count = len(short_returns)
                        decisions.append(
                            {
                                "date": date,
                                "variant": variant,
                                "net_pnl": pnl,
                                "valid_signal_day": valid,
                                "long_count": long_count,
                                "short_count": short_count,
                            }
                        )
                    daily = pd.DataFrame(decisions)
                    if daily.empty:
                        continue
                    decision_rows.extend(decisions)
                    monthly = (
                        daily[daily["date"].ge("2024-11-01")]
                        .assign(month=lambda x: x["date"].dt.to_period("M"))
                        .groupby("month")["net_pnl"]
                        .sum()
                        .reindex(months, fill_value=0.0)
                    )
                    recent = daily[daily["date"].ge("2024-11-01")]
                    dd, recovery, unresolved = max_drawdown_and_recovery(recent)
                    total = float(recent["net_pnl"].sum())
                    active = recent[recent["net_pnl"].ne(0)]
                    row = {
                        "variant": variant,
                        "direction": direction,
                        "horizon": horizon,
                        "portfolio": portfolio,
                        "cost_bps_per_side": cost,
                        "full_net_simple_return": total,
                        "standard_max_drawdown": dd,
                        "max_recovery_days": recovery,
                        "recovery_unresolved": unresolved,
                        "signal_days": int(len(recent)),
                        "post_signal_invalid_days": int(
                            (~recent["valid_signal_day"]).sum()
                        ),
                        "active_days_recent": int(len(active)),
                        "top_5_day_profit_share": (
                            float(active["net_pnl"].nlargest(5).sum() / total)
                            if total > 0 else np.nan
                        ),
                    }
                    for label, start in WINDOW_STARTS.items():
                        subset = monthly[monthly.index >= start.to_period("M")]
                        row[f"average_month_{label}"] = float(subset.mean())
                        row[f"negative_months_{label}"] = int((subset < 0).sum())
                        row[f"zero_months_{label}"] = int((subset == 0).sum())
                    summary_rows.append(row)
                    for month, pnl in monthly.items():
                        monthly_rows.append(
                            {
                                "variant": variant,
                                "month": str(month),
                                "net_pnl": float(pnl),
                            }
                        )
    return (
        pd.DataFrame(summary_rows),
        pd.DataFrame(monthly_rows),
        pd.DataFrame(decision_rows),
        pd.DataFrame(position_rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--readiness-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    events = pd.read_parquet(args.readiness_dir / "event_readiness.parquet")
    events["date"] = pd.to_datetime(events["date"])
    if events["date"].max() > CUTOFF:
        raise RuntimeError("Sealed holdout row loaded")
    marks = aggregate_marks(events, args.readiness_dir / "regular_minutes.parquet")
    marks["date"] = pd.to_datetime(marks["date"])
    signals = prepare_signals(events, marks)
    variants, monthly, decisions, positions = build_results(signals)
    if len(variants) != 96:
        raise RuntimeError(f"Expected 96 variants, got {len(variants)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    signals.to_parquet(args.output_dir / "signals.parquet", index=False)
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    decisions.to_parquet(args.output_dir / "decisions.parquet", index=False)
    positions.to_parquet(args.output_dir / "positions.parquet", index=False)
    contract = {
        "command": (
            "python campaigns/CAM-0006/src/run0001.py "
            "--readiness-dir campaigns/CAM-0006/artifacts/readiness "
            "--output-dir campaigns/CAM-0006/artifacts/RUN-0001"
        ),
        "resolved_defaults": {
            "liquidity_floor": LIQUIDITY_FLOOR,
            "tail_each_side": TAIL,
            "minimum_absolute_gap": MIN_GAP,
            "directions": list(DIRECTIONS),
            "horizons": list(HORIZONS),
            "portfolios": list(PORTFOLIOS),
            "cost_bps_per_side": list(COSTS),
            "short_stop_fraction": 0.02,
            "adverse_stop_slippage_bps": 10,
        },
        "executed_variant_count": int(len(variants)),
        "causal_universe_events": int(len(signals)),
        "continuation_signal_events": int(signals["continuation_signal"].sum()),
        "absorption_signal_events": int(signals["absorption_signal"].sum()),
        "max_loaded_date": str(events["date"].max().date()),
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2), encoding="utf-8"
    )
    print(
        variants.sort_values(
            ["average_month_15m", "standard_max_drawdown"],
            ascending=[False, True],
        )
        .head(30)
        .to_string(index=False)
    )
    print(json.dumps(contract, indent=2))


if __name__ == "__main__":
    main()
