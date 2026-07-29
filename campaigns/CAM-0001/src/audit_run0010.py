from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import pandas as pd

from cam0001 import CATALOG, CUTOFF, HOLDOUT_START, _max_drawdown_and_recovery


QUOTE_ROOT = Path(
    r"D:\AlgoResearch\data\raw\alpaca\market\stocks\quotes_sip\schema_v1"
)
SCENARIOS = {
    "bar_open_0m": (0, False),
    "bar_open_1m": (1, False),
    "bar_open_5m": (5, False),
    "bar_open_30m": (30, False),
    "bar_adverse_1m": (1, True),
    "bar_adverse_5m": (5, True),
}


def load_minute_bars(required_dates: list[str], adjusted_daily: pd.DataFrame) -> pd.DataFrame:
    con = duckdb.connect(str(CATALOG), read_only=True)
    try:
        frame = con.execute(
            """
            SELECT symbol, date, timestamp, open, high, low, close, volume,
                   trade_count, feed, adjustment
            FROM bars_1m
            WHERE date <= CAST(? AS DATE)
              AND date IN (SELECT CAST(UNNEST(?) AS DATE))
              AND symbol IN ('TQQQ', 'SOXL')
              AND feed = 'sip'
              AND adjustment = 'raw'
            ORDER BY date, symbol, timestamp
            """,
            [CUTOFF.date().isoformat(), required_dates],
        ).fetchdf()
    finally:
        con.close()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["et"] = frame["timestamp"].dt.tz_convert("America/New_York")
    con = duckdb.connect(str(CATALOG), read_only=True)
    try:
        raw_daily = con.execute(
            """
            SELECT symbol, date, close AS raw_daily_close
            FROM bars_1d
            WHERE date <= CAST(? AS DATE)
              AND date IN (SELECT CAST(UNNEST(?) AS DATE))
              AND symbol IN ('TQQQ', 'SOXL')
              AND feed = 'sip' AND adjustment = 'raw'
            """,
            [CUTOFF.date().isoformat(), required_dates],
        ).fetchdf()
    finally:
        con.close()
    raw_daily["date"] = pd.to_datetime(raw_daily["date"])
    adjusted = adjusted_daily[
        adjusted_daily["symbol"].isin(["TQQQ", "SOXL"])
        & adjusted_daily["date"].dt.date.astype(str).isin(required_dates)
    ][["symbol", "date", "close"]].rename(columns={"close": "adjusted_daily_close"})
    factors = adjusted.merge(raw_daily, on=["symbol", "date"], how="inner")
    factors["split_factor"] = factors["adjusted_daily_close"] / factors["raw_daily_close"]
    frame = frame.merge(factors[["symbol", "date", "split_factor"]], on=["symbol", "date"], how="left")
    if frame["split_factor"].isna().any():
        raise RuntimeError("missing daily split factor for raw minute bars")
    for column in ["open", "high", "low", "close"]:
        frame[column] *= frame["split_factor"]
    frame["adjustment"] = "split_reconstructed_from_daily_factor"
    return frame


def quote_coverage(required_pairs: pd.DataFrame) -> dict:
    dates = sorted(required_pairs["date"].dt.date.astype(str).unique())
    files = [QUOTE_ROOT / f"session_date={date}" / "coverage.parquet" for date in dates]
    missing_files = [str(path) for path in files if not path.exists()]
    available_files = [str(path) for path in files if path.exists()]
    covered = pd.DataFrame(columns=["symbol", "session_date"])
    if available_files:
        con = duckdb.connect()
        try:
            covered = con.execute(
                """
                SELECT DISTINCT symbol, session_date
                FROM read_parquet(?)
                WHERE session_date <= CAST(? AS DATE)
                  AND symbol IN ('TQQQ', 'SOXL')
                """,
                [available_files, CUTOFF.date().isoformat()],
            ).fetchdf()
        finally:
            con.close()
    covered["session_date"] = pd.to_datetime(covered["session_date"])
    requested = set(map(tuple, required_pairs[["symbol", "date"]].to_numpy()))
    observed = set(map(tuple, covered[["symbol", "session_date"]].to_numpy()))
    return {
        "required_symbol_dates": len(requested),
        "covered_symbol_dates": len(requested & observed),
        "coverage_fraction": len(requested & observed) / len(requested) if requested else 0.0,
        "missing_coverage_files": missing_files,
        "quote_fill_stage": (
            "eligible" if requested and requested <= observed else "not_executable_missing_etf_quotes"
        ),
        "note": (
            "The local SIP quote archive begins 2025-05-01 but its coverage files "
            "contain QQQ-member stocks, not TQQQ or SOXL, on the required dates."
        ),
    }


def select_bar(bars: pd.DataFrame, symbol: str, date: pd.Timestamp, minute: int) -> pd.Series:
    target_minutes = 9 * 60 + 30 + minute
    subset = bars[(bars["symbol"] == symbol) & (bars["date"] == date)].copy()
    subset["minute_of_day"] = subset["et"].dt.hour * 60 + subset["et"].dt.minute
    exact = subset[subset["minute_of_day"] == target_minutes]
    if exact.empty:
        raise RuntimeError(f"missing {symbol} {date.date()} minute {minute}")
    return exact.iloc[0]


def replay(
    trades: pd.DataFrame,
    minute_bars: pd.DataFrame,
    daily_frame: pd.DataFrame,
    minute: int,
    adverse: bool,
) -> tuple[pd.DataFrame, dict]:
    dates = pd.Index(sorted(daily_frame["date"].unique()))
    daily_opens = daily_frame.pivot(index="date", columns="symbol", values="open").sort_index()
    output = pd.DataFrame({"date": dates, "gross_pnl": 0.0, "cost": 0.0, "utilization": 0.0})
    date_to_i = {date: i for i, date in enumerate(dates)}
    fill_rows = []
    for trade in trades.itertuples():
        entry_bar = select_bar(minute_bars, trade.symbol, trade.entry_date, minute)
        exit_bar = select_bar(minute_bars, trade.symbol, trade.exit_date, minute)
        entry_fill = float(entry_bar.high if adverse else entry_bar.open)
        exit_fill = float(exit_bar.low if adverse else exit_bar.open)
        units = float(trade.weight) / entry_fill
        entry_i = date_to_i[trade.entry_date]
        exit_i = date_to_i[trade.exit_date]
        output.loc[entry_i, "cost"] += float(trade.weight) * 0.0005
        output.loc[exit_i, "cost"] += float(trade.weight) * 0.0005
        output.loc[entry_i:exit_i - 1, "utilization"] += float(trade.weight)
        prior = entry_fill
        for i in range(entry_i + 1, exit_i + 1):
            price = exit_fill if i == exit_i else float(daily_opens.loc[dates[i], trade.symbol])
            output.loc[i, "gross_pnl"] += units * (price - prior)
            prior = price
        fill_rows.append({
            "symbol": trade.symbol,
            "entry_date": trade.entry_date.date().isoformat(),
            "exit_date": trade.exit_date.date().isoformat(),
            "weight": float(trade.weight),
            "entry_fill": entry_fill,
            "exit_fill": exit_fill,
            "entry_bar_volume": int(entry_bar.volume),
            "exit_bar_volume": int(exit_bar.volume),
            "entry_bar_trade_count": int(entry_bar.trade_count),
            "exit_bar_trade_count": int(exit_bar.trade_count),
        })
    output["net_pnl"] = output["gross_pnl"] - output["cost"]
    metric_output = output[output["date"] >= trades["entry_date"].min()].copy()
    monthly = metric_output.assign(
        month=metric_output["date"].dt.to_period("M")
    ).groupby("month")["net_pnl"].sum()
    max_dd, recovery, unresolved = _max_drawdown_and_recovery(metric_output)
    fills = pd.DataFrame(fill_rows)
    metrics = {
        "net_simple_return": float(metric_output["net_pnl"].sum()),
        "average_monthly_net_simple_return": float(monthly.mean()),
        "median_monthly_net_simple_return": float(monthly.median()),
        "negative_month_count": int((monthly < 0).sum()),
        "standard_max_drawdown": max_dd,
        "max_recovery_days": recovery,
        "ending_drawdown_unrecovered": unresolved,
        "average_utilization": float(metric_output["utilization"].mean()),
        "minimum_entry_bar_volume": int(fills["entry_bar_volume"].min()),
        "median_entry_bar_volume": float(fills["entry_bar_volume"].median()),
        "minimum_entry_bar_trade_count": int(fills["entry_bar_trade_count"].min()),
        "monthly": {str(k): float(v) for k, v in monthly.items()},
    }
    return fills, metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-trades", type=Path, required=True)
    parser.add_argument("--daily-cache", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    trades = pd.read_csv(
        args.candidate_trades, parse_dates=["decision_date", "entry_date", "exit_date"]
    )
    trades = trades[trades["entry_date"] >= "2025-05-01"].copy()
    daily_frame = pd.read_parquet(args.daily_cache)
    daily_frame["date"] = pd.to_datetime(daily_frame["date"])
    if daily_frame["date"].max() > CUTOFF or int((daily_frame["date"] >= HOLDOUT_START).sum()):
        raise RuntimeError("holdout check failed")
    required = pd.concat([
        trades[["symbol", "entry_date"]].rename(columns={"entry_date": "date"}),
        trades[["symbol", "exit_date"]].rename(columns={"exit_date": "date"}),
    ]).drop_duplicates()
    coverage = quote_coverage(required)
    required_dates = sorted(required["date"].dt.date.astype(str).unique())
    minute_bars = load_minute_bars(required_dates, daily_frame)
    if minute_bars.empty:
        raise RuntimeError("no SIP minute bars loaded")
    if minute_bars["date"].max() > CUTOFF or int((minute_bars["date"] >= HOLDOUT_START).sum()):
        raise RuntimeError("minute-bar holdout check failed")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    all_fills = []
    for name, (minute, adverse) in SCENARIOS.items():
        fills, metrics = replay(trades, minute_bars, daily_frame, minute, adverse)
        fills["scenario"] = name
        all_fills.append(fills)
        results[name] = metrics
    pd.concat(all_fills, ignore_index=True).to_csv(args.output_dir / "fills.csv", index=False)
    report = {
        "quote_coverage": coverage,
        "quote_fill_results": None,
        "quote_fill_blocker": (
            "No TQQQ/SOXL coverage in the local SIP quote archive on required "
            "candidate symbol-dates; quote fills cannot be honestly simulated."
        ),
        "sip_minute_bar_results": results,
        "minute_bar_rows_loaded": int(len(minute_bars)),
        "minute_bar_adjustment": (
            "Raw SIP minute OHLC multiplied by the same-date split/raw daily "
            "close factor from the cutoff-bounded reconciled daily panel."
        ),
        "minute_bar_min_date": minute_bars["date"].min().date().isoformat(),
        "minute_bar_max_date": minute_bars["date"].max().date().isoformat(),
        "holdout_rows_loaded": int((minute_bars["date"] >= HOLDOUT_START).sum()),
        "capacity_note": (
            "One-minute volume/trade counts support only a coarse liquidity check. "
            "Normalized-capital results do not establish dollar capacity, spread, "
            "queue position, or displayed NBBO size."
        ),
    }
    (args.output_dir / "execution_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "executed_bar_variants": len(SCENARIOS),
        "expected_bar_variants": 6,
        "quote_stage": coverage["quote_fill_stage"],
        "required_quote_symbol_dates": coverage["required_symbol_dates"],
        "covered_quote_symbol_dates": coverage["covered_symbol_dates"],
        "loaded_max_date": report["minute_bar_max_date"],
        "holdout_rows_loaded": report["holdout_rows_loaded"],
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(contract, indent=2))


if __name__ == "__main__":
    main()
