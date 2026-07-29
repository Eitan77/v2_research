from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import exchange_calendars as xcals
import numpy as np
import pandas as pd

from cam0005 import CUTOFF, stable_frame_hash, validate_cutoff


CATALOG = r"D:\AlgoResearch\data\catalog.duckdb"
SYMBOLS = ["QQQ", "TQQQ", "SQQQ", "SMH", "SOXL", "SOXS"]
UNDERLYINGS = ["QQQ", "SMH"]
START = "2023-01-01"


def load_targeted_rows() -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(CATALOG, read_only=True)
    symbols = ",".join(f"'{symbol}'" for symbol in SYMBOLS)
    minutes = con.execute(
        f"""
        SELECT symbol, timestamp, open, high, low, close, volume, trade_count,
               vwap, feed, adjustment, date
        FROM bars_1m
        WHERE symbol IN ({symbols})
          AND date BETWEEN DATE '{START}' AND DATE '2026-04-30'
          AND feed = 'sip' AND adjustment = 'raw'
        """
    ).fetchdf()
    daily = con.execute(
        f"""
        SELECT symbol, date, open, high, low, close, volume, feed, adjustment
        FROM bars_1d
        WHERE symbol IN ({symbols})
          AND date BETWEEN DATE '{START}' AND DATE '2026-04-30'
          AND feed = 'sip' AND adjustment IN ('raw', 'split')
        """
    ).fetchdf()
    con.close()
    minutes["timestamp"] = pd.to_datetime(minutes["timestamp"], utc=True)
    minutes["local_ts"] = minutes["timestamp"].dt.tz_convert(
        "America/New_York"
    )
    minutes["session"] = minutes["local_ts"].dt.tz_localize(None).dt.normalize()
    minutes["minute"] = minutes["local_ts"].dt.strftime("%H:%M")
    regular = minutes["minute"].between("09:30", "15:59")
    signal = minutes["symbol"].isin(UNDERLYINGS) & minutes["minute"].between(
        "15:00", "15:59"
    )
    entry = minutes["minute"].isin(["15:56", "15:57", "15:59"])
    opening = minutes["minute"].between("09:30", "09:34")
    minutes = minutes[regular & (signal | entry | opening)].copy()
    daily["date"] = pd.to_datetime(daily["date"])
    return minutes, daily


def split_adjust(minutes: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    official = daily.pivot(
        index=["date", "symbol"], columns="adjustment", values="close"
    ).reset_index()
    if official.duplicated(["date", "symbol"]).any():
        raise RuntimeError("duplicate official daily adjustment keys")
    if not {"raw", "split"}.issubset(official.columns):
        raise RuntimeError("official raw/split daily pair unavailable")
    factors = official.rename(columns={"date": "session"}).copy()
    factors["adjustment_factor"] = factors["split"] / factors["raw"]
    if factors["adjustment_factor"].dropna().le(0).any():
        raise RuntimeError("invalid official split/raw adjustment factor")
    factors = factors[np.isfinite(factors["adjustment_factor"])].copy()
    result = minutes.merge(
        factors[["session", "symbol", "adjustment_factor"]],
        on=["session", "symbol"],
        how="inner",
        validate="many_to_one",
    )
    for column in ["open", "high", "low", "close", "vwap"]:
        result[column] = result[column] * result["adjustment_factor"]
    return result


def coverage_report(minutes: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    calendar = xcals.get_calendar("XNYS")
    sessions = pd.DatetimeIndex(
        calendar.sessions_in_range("2023-01-03", CUTOFF)
    ).tz_localize(None)
    grid = pd.MultiIndex.from_product(
        [sessions, SYMBOLS], names=["session", "symbol"]
    ).to_frame(index=False)
    observed = (
        minutes.groupby(["session", "symbol"])
        .agg(
            minute_count=("minute", "size"),
            has_1530=("minute", lambda x: "15:30" in set(x)),
            has_1500=("minute", lambda x: "15:00" in set(x)),
            has_1554=("minute", lambda x: "15:54" in set(x)),
            has_1556=("minute", lambda x: "15:56" in set(x)),
            has_1557=("minute", lambda x: "15:57" in set(x)),
            has_1559=("minute", lambda x: "15:59" in set(x)),
            has_0930=("minute", lambda x: "09:30" in set(x)),
            has_0934=("minute", lambda x: "09:34" in set(x)),
        )
        .reset_index()
    )
    coverage = grid.merge(
        observed, on=["session", "symbol"], how="left", validate="one_to_one"
    )
    bool_columns = [column for column in coverage if column.startswith("has_")]
    coverage[bool_columns] = coverage[bool_columns].fillna(False)
    coverage["minute_count"] = coverage["minute_count"].fillna(0).astype(int)
    underlying = coverage["symbol"].isin(UNDERLYINGS)
    coverage["signal_complete"] = (
        ~underlying | (coverage["has_1500"] & coverage["has_1554"])
    )
    coverage["entry_complete"] = (
        coverage["has_1556"]
        & coverage["has_1557"]
        & coverage["has_1559"]
    )
    coverage["opening_complete"] = (
        coverage["has_0930"] & coverage["has_0934"]
    )
    report = {
        "session_symbol_rows": int(len(coverage)),
        "sessions": int(len(sessions)),
        "signal_incomplete_underlying_rows": int(
            (~coverage.loc[underlying, "signal_complete"]).sum()
        ),
        "entry_incomplete_rows": int((~coverage["entry_complete"]).sum()),
        "opening_incomplete_rows": int((~coverage["opening_complete"]).sum()),
        "zero_targeted_rows": int(coverage["minute_count"].eq(0).sum()),
    }
    return coverage, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    minutes, daily = load_targeted_rows()
    minutes = split_adjust(minutes, daily)
    validate_cutoff(minutes)
    validate_cutoff(daily.rename(columns={"date": "session"}))
    if minutes[["open", "high", "low", "close"]].isna().any(axis=None):
        raise RuntimeError("null OHLC in targeted minute rows")
    if minutes.duplicated(["symbol", "local_ts"]).any():
        raise RuntimeError("duplicate targeted minute keys")
    coverage, coverage_summary = coverage_report(minutes)
    report = {
        "status": "passed",
        "catalog": CATALOG,
        "symbols": SYMBOLS,
        "source": "local catalog cutoff-bounded SIP raw one-minute plus SIP split daily",
        "minute_rows": int(len(minutes)),
        "daily_rows": int(len(daily)),
        "min_session": str(minutes["session"].min().date()),
        "max_loaded_date": str(minutes["session"].max().date()),
        "holdout_rows_loaded": int(
            minutes["session"].ge("2026-05-01").sum()
        ),
        "adjustment_factor_min": float(minutes["adjustment_factor"].min()),
        "adjustment_factor_max": float(minutes["adjustment_factor"].max()),
        "coverage": coverage_summary,
        "hashes": {
            "minutes": stable_frame_hash(
                minutes, ["session", "symbol", "local_ts"]
            ),
            "daily": stable_frame_hash(daily, ["date", "symbol"]),
        },
    }
    if report["max_loaded_date"] > "2026-04-30":
        raise RuntimeError("cutoff failed")
    minutes.to_parquet(args.output_dir / "targeted_minutes.parquet", index=False)
    daily.to_parquet(args.output_dir / "split_daily.parquet", index=False)
    coverage.to_parquet(args.output_dir / "coverage.parquet", index=False)
    (args.output_dir / "readiness.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
