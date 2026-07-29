from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from cam0001 import (
    CATALOG,
    CUTOFF,
    HOLDOUT_START,
    RunConfig,
    load_cutoff_bars,
    simulate,
    summarize,
)


EXPANDED_SYMBOLS = ["QQQ", "SMH", "TQQQ", "SOXL", "SQQQ", "SOXS"]


def stable_hash(frame: pd.DataFrame) -> str:
    ordered = frame.sort_values(["date", "symbol"]).reset_index(drop=True)
    return hashlib.sha256(
        ordered.to_csv(index=False, float_format="%.12g", lineterminator="\n").encode("utf-8")
    ).hexdigest()


def expanded_readiness(cache: Path, output: Path) -> tuple[pd.DataFrame, dict]:
    frame, reconciliation = load_cutoff_bars(CATALOG, EXPANDED_SYMBOLS)
    failures = []
    symbols = sorted(frame["symbol"].unique())
    if symbols != sorted(EXPANDED_SYMBOLS):
        failures.append(f"symbol mismatch: {symbols}")
    if frame["date"].max() > CUTOFF:
        failures.append("maximum date exceeds cutoff")
    holdout_rows = int((frame["date"] >= HOLDOUT_START).sum())
    if holdout_rows:
        failures.append(f"{holdout_rows} holdout rows")
    duplicates = int(frame.duplicated(["symbol", "date"]).sum())
    if duplicates:
        failures.append(f"{duplicates} duplicate symbol-date rows")
    invalid = int(
        (
            frame[["open", "high", "low", "close"]].isna().any(axis=1)
            | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
            | (frame["high"] < frame[["open", "close"]].max(axis=1))
            | (frame["low"] > frame[["open", "close"]].min(axis=1))
        ).sum()
    )
    if invalid:
        failures.append(f"{invalid} invalid OHLC rows")
    date_sets = {s: set(g["date"]) for s, g in frame.groupby("symbol")}
    shared = set.intersection(*date_sets.values())
    date_attrition = {s: len(dates - shared) for s, dates in date_sets.items()}
    if any(date_attrition.values()):
        failures.append(f"unaligned dates: {date_attrition}")
    report = {
        "status": "failed" if failures else "passed",
        "symbols": symbols,
        "source_filter": (
            "bars_1d date <= 2026-04-30, feed=sip, adjustment in raw/split, "
            "symbols QQQ/SMH/TQQQ/SOXL/SQQQ/SOXS"
        ),
        "max_loaded_date": frame["date"].max().date().isoformat(),
        "holdout_rows_loaded": holdout_rows,
        "rows": int(len(frame)),
        "sessions_per_symbol": {
            s: int(g["date"].nunique()) for s, g in frame.groupby("symbol")
        },
        "date_attrition_vs_shared_calendar": date_attrition,
        "invalid_ohlc_rows": invalid,
        "duplicate_symbol_dates": duplicates,
        "reconciliation": reconciliation,
        "loaded_frame_sha256": stable_hash(frame),
        "failures": failures,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    frame.to_parquet(cache, index=False)
    if failures:
        raise RuntimeError("expanded readiness failed: " + "; ".join(failures))
    return frame, report


def simulate_switch(frame: pd.DataFrame, market_sma: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    if frame["date"].max() > CUTOFF or int((frame["date"] >= HOLDOUT_START).sum()):
        raise RuntimeError("switch holdout check failed")
    opens = frame.pivot(index="date", columns="symbol", values="open").sort_index()
    closes = frame.pivot(index="date", columns="symbol", values="close").sort_index()
    dates = opens.index
    groups = {"bull": ["TQQQ", "SOXL"], "bear": ["SQQQ", "SOXS"]}
    momentum = closes[sum(groups.values(), [])] / closes[sum(groups.values(), [])].shift(5) - 1
    sma = closes["QQQ"].rolling(market_sma, min_periods=market_sma).mean()
    above_rising = (closes["QQQ"] > sma) & (sma > sma.shift(5))
    below_falling = (closes["QQQ"] < sma) & (sma < sma.shift(5))
    daily = pd.DataFrame({"date": dates, "gross_pnl": 0.0, "cost": 0.0, "utilization": 0.0})
    trades = []
    decision_i = max(5, market_sma - 1)
    side_cost = 5.0 / 10_000.0
    while True:
        entry_i = decision_i + 1
        exit_i = entry_i + 10
        if exit_i >= len(dates):
            break
        regime = "bull" if bool(above_rising.iloc[decision_i]) else (
            "bear" if bool(below_falling.iloc[decision_i]) else None
        )
        ranked = (
            momentum.iloc[decision_i][groups[regime]].dropna().sort_values(ascending=False)
            if regime else pd.Series(dtype=float)
        )
        chosen = ranked[ranked > 0].head(2).index.tolist()
        if not chosen:
            decision_i += 1
            continue
        weight = 1.0 / len(chosen)
        daily.loc[entry_i, "cost"] += side_cost
        daily.loc[exit_i, "cost"] += side_cost
        daily.loc[entry_i:exit_i - 1, "utilization"] = 1.0
        for symbol in chosen:
            entry_px = float(opens.iloc[entry_i][symbol])
            exit_px = float(opens.iloc[exit_i][symbol])
            units = weight / entry_px
            for i in range(entry_i + 1, exit_i + 1):
                daily.loc[i, "gross_pnl"] += units * (
                    float(opens.iloc[i][symbol]) - float(opens.iloc[i - 1][symbol])
                )
            gross = weight * (exit_px / entry_px - 1)
            trades.append({
                "decision_date": dates[decision_i],
                "entry_date": dates[entry_i],
                "exit_date": dates[exit_i],
                "symbol": symbol,
                "weight": weight,
                "signal": float(momentum.iloc[decision_i][symbol]),
                "entry_price": entry_px,
                "exit_price": exit_px,
                "gross_return_contribution": gross,
                "net_return_contribution": gross - weight * 2 * side_cost,
                "regime": regime,
            })
        decision_i = exit_i - 1
    daily["net_pnl"] = daily["gross_pnl"] - daily["cost"]
    daily["equity"] = 1 + daily["net_pnl"].cumsum()
    daily["qqq_open_return"] = opens["QQQ"].pct_change().fillna(0).to_numpy()
    trades_df = pd.DataFrame(trades)
    config = RunConfig(
        trade_symbols=("TQQQ", "SOXL", "SQQQ", "SOXS"),
        lookback=5, market_sma=market_sma, holding_sessions=10, breadth=2,
        require_sma_rising=True,
    )
    metrics = summarize(daily, trades_df, config)
    metrics["regime_contribution"] = (
        trades_df.groupby("regime")["net_return_contribution"].sum().to_dict()
        if len(trades_df) else {}
    )
    return daily, trades_df, metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--readiness-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    frame, ready = expanded_readiness(args.cache, args.readiness_json)
    specs = []
    for sma in [20, 50]:
        specs.extend([
            (f"sma{sma}_bull_leveraged", RunConfig(
                trade_symbols=("TQQQ", "SOXL"), lookback=5, market_sma=sma,
                holding_sessions=10, breadth=2, require_sma_rising=True,
            )),
            (f"sma{sma}_bull_unleveraged", RunConfig(
                trade_symbols=("QQQ", "SMH"), lookback=5, market_sma=sma,
                holding_sessions=10, breadth=2, require_sma_rising=True,
            )),
            (f"sma{sma}_bear_inverse_below", RunConfig(
                trade_symbols=("SQQQ", "SOXS"), lookback=5, market_sma=sma,
                holding_sessions=10, breadth=2, market_trend_direction="below",
            )),
            (f"sma{sma}_bear_inverse_falling", RunConfig(
                trade_symbols=("SQQQ", "SOXS"), lookback=5, market_sma=sma,
                holding_sessions=10, breadth=2, market_trend_direction="below",
                require_sma_falling=True,
            )),
        ])
    rows = []
    details = {}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, config in specs:
        daily, trades, metrics = simulate(frame, config)
        rows.append(flatten(name, metrics, config))
        details[name] = metrics
    for sma in [20, 50]:
        name = f"sma{sma}_regime_switch"
        daily, trades, metrics = simulate_switch(frame, sma)
        rows.append(flatten(name, metrics, None))
        details[name] = metrics
        detail = args.output_dir / name
        detail.mkdir(exist_ok=True)
        daily.to_csv(detail / "daily.csv", index=False)
        trades.to_csv(detail / "trades.csv", index=False)
    if len(rows) != 10:
        raise RuntimeError("RUN-0007 executed variant count mismatch")
    pd.DataFrame(rows).to_csv(args.output_dir / "universe_family.csv", index=False)
    (args.output_dir / "metrics.json").write_text(
        json.dumps(details, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "executed_variant_count": len(rows),
        "expected_variant_count": 10,
        "expanded_readiness_status": ready["status"],
        "loaded_frame_sha256": ready["loaded_frame_sha256"],
        "loaded_max_date": ready["max_loaded_date"],
        "holdout_rows_loaded": ready["holdout_rows_loaded"],
        "direct_shorts_used": False,
        "inverse_note": "SQQQ/SOXS are purchased long; no direct short position exists.",
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(contract, indent=2))


def flatten(name: str, metrics: dict, config: RunConfig | None) -> dict:
    row = {"variant": name}
    if config is not None:
        row.update(asdict(config))
    row.update({
        "full_net": metrics["net_full_period_simple_return"],
        "full_max_dd": metrics["standard_max_drawdown"],
        "full_recovery_days": metrics["max_full_recovery_time_days"],
        "full_decisions": metrics["independent_entry_decisions"],
        "full_utilization": metrics["average_capital_utilization"],
        "full_beta": metrics["market_beta_on_active_days"],
    })
    for label in ["18m", "15m", "12m"]:
        window = metrics["recent_windows"][label]
        row.update({
            f"{label}_net": window["net_simple_return"],
            f"{label}_avg_month": window["average_monthly_net_simple_return"],
            f"{label}_median_month": window["median_monthly_net_simple_return"],
            f"{label}_negative_months": window["negative_month_count"],
            f"{label}_max_dd": window["standard_max_drawdown"],
            f"{label}_recovery_days": window["max_full_recovery_time_days"],
            f"{label}_decisions": window["independent_entry_decisions"],
        })
    return row


if __name__ == "__main__":
    main()
