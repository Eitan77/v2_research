from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cam0006 import (
    CUTOFF,
    marketable_long_return,
    max_drawdown_and_recovery,
    protected_long_exit,
)


SELECTORS = (
    ("tail05_all", 0.05, None),
    ("tail05_anomaly1", 0.05, 1.0),
    ("tail10_anomaly1", 0.10, 1.0),
    ("tail15_anomaly1", 0.15, 1.0),
)
ENTRY_MINUTES = ("09:31", "09:32", "09:33", "09:35")
RISK_RULES = ("none", "stop1", "stop2", "stop3", "auction_failure")
COSTS = (5, 10, 20)
RECENT_START = pd.Timestamp("2024-11-01")
MONTHS = pd.period_range("2024-11", "2026-04", freq="M")
WINDOW_STARTS = {
    "18m": pd.Timestamp("2024-11-01"),
    "15m": pd.Timestamp("2025-02-01"),
    "12m": pd.Timestamp("2025-05-01"),
}
BLOCKS = (
    ("block_1", pd.Timestamp("2024-11-01"), pd.Timestamp("2025-04-30")),
    ("block_2", pd.Timestamp("2025-05-01"), pd.Timestamp("2025-10-31")),
    ("block_3", pd.Timestamp("2025-11-01"), pd.Timestamp("2026-04-30")),
)


def load_paths(minutes_path: Path, signals_path: Path) -> pd.DataFrame:
    con = duckdb.connect()
    try:
        result = con.execute(
            """
            WITH eligible AS (
              SELECT DISTINCT symbol, CAST(date AS DATE) AS date
              FROM read_parquet(?)
              WHERE date >= DATE '2024-11-01'
                AND date <= DATE '2026-04-30'
                AND raw_gap <= -0.005
                AND first_minute_return > 0
                AND entry_open > 0
                AND gap_rank <= 0.15
                AND first_minute_return / (-raw_gap) >= 0.25
            )
            SELECT m.symbol, CAST(m.date AS DATE) AS date, m.minute,
                   m.open, m.high, m.low, m.close, m.final_exit_minute,
                   m.distinct_minutes
            FROM (
              SELECT *, count(*) OVER (PARTITION BY symbol, date) AS distinct_minutes
              FROM read_parquet(?)
              WHERE date >= DATE '2024-11-01'
                AND date <= DATE '2026-04-30'
                AND minute >= '09:31'
            ) m
            INNER JOIN eligible e USING (symbol, date)
            WHERE m.minute <= m.final_exit_minute
            ORDER BY m.symbol, m.date, m.minute
            """,
            [str(signals_path), str(minutes_path)],
        ).fetch_df()
    finally:
        con.close()
    result["date"] = pd.to_datetime(result["date"])
    return result


def selector_mask(
    frame: pd.DataFrame, tail: float, anomaly_minimum: float | None
) -> pd.Series:
    mask = (
        frame["raw_gap"].le(-0.005)
        & frame["first_minute_return"].gt(0)
        & frame["entry_open"].gt(0)
        & frame["gap_rank"].le(tail)
        & frame["reclaim_fraction"].ge(0.25)
    )
    if anomaly_minimum is not None:
        mask &= frame["auction_anomaly"].ge(anomaly_minimum)
    return mask


def event_exit(
    event: pd.Series,
    path: pd.DataFrame,
    entry_minute: str,
    risk_rule: str,
) -> tuple[float, float, bool, bool] | None:
    entry_rows = path[path["minute"].eq(entry_minute)]
    final_rows = path[path["minute"].eq(event["final_exit_minute"])]
    if len(entry_rows) != 1 or len(final_rows) != 1:
        return None
    entry = float(entry_rows.iloc[0]["open"])
    planned_exit = float(final_rows.iloc[0]["open"])
    if not np.isfinite(entry) or entry <= 0 or not np.isfinite(planned_exit):
        return None
    active = path[
        path["minute"].ge(entry_minute)
        & path["minute"].le(event["final_exit_minute"])
    ].sort_values("minute")
    stopped = False
    mechanism_failed = False
    if risk_rule.startswith("stop"):
        stop_fraction = int(risk_rule[-1]) / 100.0
        exit_price, stopped = protected_long_exit(
            entry,
            planned_exit,
            active[["open", "low"]].itertuples(index=False, name=None),
            stop_fraction,
            10.0,
        )
    elif risk_rule == "auction_failure":
        exit_price = planned_exit
        trigger = active[
            active["minute"].lt(event["final_exit_minute"])
            & active["close"].le(float(event["auction_price_raw"]))
        ]
        if not trigger.empty:
            trigger_minute = str(trigger.iloc[0]["minute"])
            following = active[active["minute"].gt(trigger_minute)]
            if not following.empty:
                exit_price = float(following.iloc[0]["open"])
                mechanism_failed = True
    else:
        exit_price = planned_exit
    return entry, float(exit_price), stopped, mechanism_failed


def evaluate(
    signals: pd.DataFrame, paths: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    path_groups = {
        (symbol, pd.Timestamp(date)): group.reset_index(drop=True)
        for (symbol, date), group in paths.groupby(["symbol", "date"], sort=False)
    }
    rows: list[dict] = []
    monthly_rows: list[dict] = []
    block_rows: list[dict] = []
    position_rows: list[dict] = []
    for selector, tail, anomaly_minimum in SELECTORS:
        selected = signals[
            selector_mask(signals, tail, anomaly_minimum)
            & signals["date"].ge(RECENT_START)
        ].copy()
        for entry_minute in ENTRY_MINUTES:
            for risk_rule in RISK_RULES:
                event_cache: dict[tuple[str, pd.Timestamp], tuple | None] = {}
                for event in selected.itertuples(index=False):
                    key = (event.symbol, pd.Timestamp(event.date))
                    path = path_groups.get(key)
                    if path is None:
                        event_cache[key] = None
                        continue
                    event_cache[key] = event_exit(
                        pd.Series(event._asdict()), path, entry_minute, risk_rule
                    )
                for cost in COSTS:
                    name = f"{selector}_{entry_minute.replace(':','')}_{risk_rule}_c{cost}"
                    daily_rows = []
                    valid_positions = []
                    stopped_events = 0
                    failed_events = 0
                    for date, group in selected.groupby("date"):
                        outcomes = []
                        for event in group.itertuples(index=False):
                            key = (event.symbol, pd.Timestamp(event.date))
                            outcome = event_cache[key]
                            if outcome is None:
                                outcomes = []
                                break
                            outcomes.append((event, outcome))
                        if not outcomes:
                            daily_rows.append(
                                {
                                    "date": pd.Timestamp(date),
                                    "net_pnl": 0.0,
                                    "valid_signal_day": False,
                                    "event_count": 0,
                                }
                            )
                            continue
                        day_returns = []
                        for event, (entry, exit_price, stopped, mechanism_failed) in outcomes:
                            event_return = marketable_long_return(entry, exit_price, cost)
                            day_returns.append(event_return)
                            stopped_events += int(stopped)
                            failed_events += int(mechanism_failed)
                        count = len(day_returns)
                        day_pnl = float(np.mean(day_returns))
                        daily_rows.append(
                            {
                                "date": pd.Timestamp(date),
                                "net_pnl": day_pnl,
                                "valid_signal_day": True,
                                "event_count": count,
                            }
                        )
                        for (event, (_, exit_price, stopped, mechanism_failed)), event_return in zip(
                            outcomes, day_returns, strict=True
                        ):
                            valid_positions.append(
                                {
                                    "variant": name,
                                    "date": pd.Timestamp(event.date),
                                    "symbol": event.symbol,
                                    "event_return": event_return,
                                    "portfolio_contribution": event_return / count,
                                    "raw_gap": event.raw_gap,
                                    "reclaim_fraction": event.reclaim_fraction,
                                    "auction_anomaly": event.auction_anomaly,
                                    "exit_price": exit_price,
                                    "stopped": stopped,
                                    "mechanism_failed": mechanism_failed,
                                }
                            )
                    daily = pd.DataFrame(daily_rows)
                    monthly = (
                        daily.assign(month=daily["date"].dt.to_period("M"))
                        .groupby("month")["net_pnl"]
                        .sum()
                        .reindex(MONTHS, fill_value=0.0)
                    )
                    dd, recovery, unresolved = max_drawdown_and_recovery(daily)
                    total = float(daily["net_pnl"].sum())
                    active = daily[daily["net_pnl"].ne(0)]
                    contribution = pd.DataFrame(valid_positions)
                    if contribution.empty:
                        symbol_count = 0
                        top_symbol_share = np.nan
                    else:
                        symbol_pnl = contribution.groupby("symbol")[
                            "portfolio_contribution"
                        ].sum()
                        symbol_count = int(len(symbol_pnl))
                        top_symbol_share = (
                            float(symbol_pnl.max() / total) if total > 0 else np.nan
                        )
                    valid_event_count = int(len(contribution))
                    row = {
                        "variant": name,
                        "selector": selector,
                        "gap_tail": tail,
                        "auction_anomaly_minimum": anomaly_minimum,
                        "entry_minute": entry_minute,
                        "risk_rule": risk_rule,
                        "cost_bps_per_side": cost,
                        "full_net_simple_return": total,
                        "standard_max_drawdown": dd,
                        "max_recovery_days": recovery,
                        "recovery_unresolved": unresolved,
                        "causal_selected_events": int(len(selected)),
                        "valid_events_recent": valid_event_count,
                        "symbol_count_recent": symbol_count,
                        "signal_days_recent": int(selected["date"].nunique()),
                        "post_signal_invalid_days": int(
                            (~daily["valid_signal_day"]).sum()
                        ),
                        "median_events_per_valid_day": (
                            float(
                                daily.loc[
                                    daily["valid_signal_day"], "event_count"
                                ].median()
                            )
                            if daily["valid_signal_day"].any()
                            else 0.0
                        ),
                        "mean_observed_path_minute_ratio": float(
                            (
                                selected["distinct_minutes"]
                                / selected["expected_minutes"]
                            ).mean()
                        )
                        if len(selected)
                        else np.nan,
                        "stop_or_failure_rate": (
                            float((stopped_events + failed_events) / valid_event_count)
                            if valid_event_count
                            else np.nan
                        ),
                        "top_5_day_profit_share": (
                            float(active["net_pnl"].nlargest(5).sum() / total)
                            if total > 0
                            else np.nan
                        ),
                        "top_10_day_profit_share": (
                            float(active["net_pnl"].nlargest(10).sum() / total)
                            if total > 0
                            else np.nan
                        ),
                        "top_symbol_profit_share": top_symbol_share,
                    }
                    for label, start in WINDOW_STARTS.items():
                        subset = monthly[monthly.index >= start.to_period("M")]
                        row[f"average_month_{label}"] = float(subset.mean())
                        row[f"negative_months_{label}"] = int((subset < 0).sum())
                        row[f"zero_months_{label}"] = int((subset == 0).sum())
                    rows.append(row)
                    for month, pnl in monthly.items():
                        monthly_rows.append(
                            {"variant": name, "month": str(month), "net_pnl": float(pnl)}
                        )
                    for block, start, end in BLOCKS:
                        sub = daily[daily["date"].between(start, end)]
                        block_rows.append(
                            {
                                "variant": name,
                                "block": block,
                                "net_pnl": float(sub["net_pnl"].sum()),
                                "signal_days": int(len(sub)),
                            }
                        )
                    position_rows.extend(valid_positions)
    return (
        pd.DataFrame(rows),
        pd.DataFrame(monthly_rows),
        pd.DataFrame(block_rows),
        pd.DataFrame(position_rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals-path", type=Path, required=True)
    parser.add_argument("--minutes-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    signals = pd.read_parquet(args.signals_path)
    signals["date"] = pd.to_datetime(signals["date"])
    if signals["date"].max() > CUTOFF:
        raise RuntimeError("Sealed holdout signal row loaded")
    signals["reclaim_fraction"] = (
        signals["first_minute_return"] / (-signals["raw_gap"])
    )
    paths = load_paths(args.minutes_path, args.signals_path)
    if paths["date"].max() > CUTOFF:
        raise RuntimeError("Sealed holdout minute row loaded")
    variants, monthly, blocks, positions = evaluate(signals, paths)
    if len(variants) != 240:
        raise RuntimeError(f"Expected 240 variants, got {len(variants)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    blocks.to_csv(args.output_dir / "blocks.csv", index=False)
    positions.to_parquet(args.output_dir / "positions.parquet", index=False)
    contract = {
        "command": (
            "python campaigns/CAM-0006/src/run0003.py "
            "--signals-path campaigns/CAM-0006/artifacts/RUN-0002/enriched_signals.parquet "
            "--minutes-path campaigns/CAM-0006/artifacts/readiness/regular_minutes.parquet "
            "--output-dir campaigns/CAM-0006/artifacts/RUN-0003"
        ),
        "resolved_defaults": {
            "selectors": [item[0] for item in SELECTORS],
            "entry_minutes": list(ENTRY_MINUTES),
            "risk_rules": list(RISK_RULES),
            "cost_bps_per_side": list(COSTS),
        },
        "executed_variant_count": int(len(variants)),
        "max_loaded_date": str(max(signals["date"].max(), paths["date"].max()).date()),
        "holdout_rows_loaded": 0,
        "loaded_path_rows": int(len(paths)),
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2), encoding="utf-8"
    )
    print(
        variants.sort_values(
            ["average_month_15m", "standard_max_drawdown"],
            ascending=[False, True],
        )
        .head(40)
        .to_string(index=False)
    )
    print(json.dumps(contract, indent=2))


if __name__ == "__main__":
    main()
