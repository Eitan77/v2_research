from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import exchange_calendars as xcals
import numpy as np
import pandas as pd


STOCKS = (
    "ADI",
    "AMD",
    "AMAT",
    "ARM",
    "ASML",
    "AVGO",
    "INTC",
    "KLAC",
    "LRCX",
    "MCHP",
    "MPWR",
    "MRVL",
    "MU",
    "NVDA",
    "NXPI",
    "ON",
    "QCOM",
    "TXN",
)
LOCAL_MINUTE_SYMBOLS = STOCKS + ("QQQ", "SMH")
ALL_DECLARED_SYMBOLS = LOCAL_MINUTE_SYMBOLS + ("SOXX",)
START = pd.Timestamp("2024-07-01")
CUTOFF = pd.Timestamp("2026-04-30")
HOLDOUT_START = pd.Timestamp("2026-05-01")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value.replace(chr(39), chr(39) * 2)}'" for value in values)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--daily-split", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--temp-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.temp_dir.mkdir(parents=True, exist_ok=True)
    raw_minute_output = args.output_dir / "minute_raw_unfiltered.parquet"
    symbols_sql = sql_values(LOCAL_MINUTE_SYMBOLS)

    connection = duckdb.connect(str(args.catalog), read_only=True)
    connection.execute(
        f"SET temp_directory='{str(args.temp_dir.resolve()).replace(chr(92), '/')}'"
    )
    output_sql = str(raw_minute_output.resolve()).replace("\\", "/").replace(
        "'", "''"
    )
    connection.execute(
        f"""
        COPY (
          SELECT
            symbol,
            CAST(timestamp AS TIMESTAMPTZ) AS timestamp,
            open, high, low, close, volume, trade_count, vwap,
            feed, adjustment, date
          FROM bars_1m
          WHERE symbol IN ({symbols_sql})
            AND feed='sip' AND adjustment='raw' AND timeframe='1Min'
            AND date BETWEEN DATE '2024-07-01' AND DATE '2026-04-30'
          QUALIFY row_number() OVER (
            PARTITION BY symbol, timestamp ORDER BY ingested_at DESC
          ) = 1
        ) TO '{output_sql}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    membership = connection.execute(
        f"""
        SELECT CAST(date AS DATE) AS date, symbol
        FROM qqq_pit_membership_daily
        WHERE symbol IN ({sql_values(STOCKS)}) AND is_member
          AND CAST(date AS DATE) BETWEEN DATE '2024-07-01' AND DATE '2026-04-30'
        ORDER BY date, symbol
        """
    ).fetch_df()
    raw_daily = connection.execute(
        f"""
        SELECT symbol, date, open, high, low, close, volume
        FROM bars_1d
        WHERE symbol IN ({sql_values(ALL_DECLARED_SYMBOLS)})
          AND feed='sip' AND adjustment='raw' AND timeframe='1Day'
          AND date BETWEEN DATE '2024-05-01' AND DATE '2026-04-30'
        QUALIFY row_number() OVER (
          PARTITION BY symbol, date ORDER BY ingested_at DESC
        ) = 1
        ORDER BY symbol, date
        """
    ).fetch_df()
    connection.close()

    minutes = pd.read_parquet(raw_minute_output)
    minutes["timestamp"] = pd.to_datetime(minutes["timestamp"], utc=True)
    minutes["date"] = pd.to_datetime(minutes["date"])
    minutes["local_ts"] = minutes["timestamp"].dt.tz_convert(
        "America/New_York"
    )
    minutes["local_date"] = minutes["local_ts"].dt.tz_localize(None).dt.normalize()
    minutes["minute_number"] = (
        minutes["local_ts"].dt.hour * 60 + minutes["local_ts"].dt.minute
    )
    calendar = xcals.get_calendar("XNYS")
    sessions = pd.DatetimeIndex(
        calendar.sessions_in_range(START.date(), CUTOFF.date())
    ).tz_localize(None)
    schedule = calendar.schedule.loc[str(START.date()) : str(CUTOFF.date())]
    close_local = schedule["close"].dt.tz_convert("America/New_York")
    close_by_date = {
        pd.Timestamp(index).tz_localize(None): value.hour * 60 + value.minute
        for index, value in close_local.items()
    }
    minutes["session_close_minute"] = minutes["local_date"].map(close_by_date)
    minutes = minutes[
        minutes["local_date"].eq(minutes["date"])
        & minutes["date"].isin(sessions)
        & minutes["minute_number"].ge(9 * 60 + 30)
        & minutes["minute_number"].lt(minutes["session_close_minute"])
    ].copy()
    minutes["minute"] = (
        (minutes["minute_number"] // 60).astype(str).str.zfill(2)
        + ":"
        + (minutes["minute_number"] % 60).astype(str).str.zfill(2)
    )
    if minutes.duplicated(["symbol", "date", "minute"]).any():
        raise RuntimeError("Duplicate targeted minute key")
    bad_minute = (
        minutes[["open", "high", "low", "close"]].isna().any(axis=1)
        | (minutes[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | minutes["high"].lt(minutes[["open", "close"]].max(axis=1))
        | minutes["low"].gt(minutes[["open", "close"]].min(axis=1))
    )
    if bad_minute.any():
        raise RuntimeError(f"Invalid minute OHLC rows: {int(bad_minute.sum())}")
    if minutes["date"].max() > CUTOFF:
        raise RuntimeError("Minute artifact crosses holdout boundary")
    minute_output = args.output_dir / "minute_regular.parquet"
    minutes[
        [
            "symbol",
            "date",
            "timestamp",
            "local_ts",
            "minute",
            "minute_number",
            "session_close_minute",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "trade_count",
            "vwap",
        ]
    ].sort_values(["date", "minute_number", "symbol"]).to_parquet(
        minute_output, index=False
    )
    raw_minute_output.unlink()

    membership["date"] = pd.to_datetime(membership["date"])
    if membership.duplicated(["date", "symbol"]).any():
        raise RuntimeError("Duplicate point-in-time membership key")
    membership_output = args.output_dir / "membership.parquet"
    membership.to_parquet(membership_output, index=False)

    split = pd.read_parquet(args.daily_split)
    split["date"] = pd.to_datetime(split["date"])
    raw_daily["date"] = pd.to_datetime(raw_daily["date"])
    if set(split["symbol"]) != set(ALL_DECLARED_SYMBOLS):
        raise RuntimeError("Split daily symbol coverage mismatch")
    split_for_local = split[split["symbol"].isin(LOCAL_MINUTE_SYMBOLS)].copy()
    daily = raw_daily.merge(
        split_for_local[
            [
                "symbol",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        ],
        on=["symbol", "date"],
        how="outer",
        suffixes=("_raw", "_split"),
        indicator=True,
        validate="one_to_one",
    )
    if daily["_merge"].ne("both").any():
        raise RuntimeError(
            f"Raw/split daily mismatch rows: {int(daily['_merge'].ne('both').sum())}"
        )
    daily["split_factor"] = daily["close_split"] / daily["close_raw"]
    if (
        daily["split_factor"].isna().any()
        or daily["split_factor"].le(0).any()
        or daily["date"].max() > CUTOFF
    ):
        raise RuntimeError("Invalid daily adjustment factor or cutoff")
    daily = daily.sort_values(["symbol", "date"]).copy()
    daily["raw_dollar_volume"] = daily["close_raw"] * daily["volume_raw"]
    daily["prior20_median_dollar_volume"] = daily.groupby("symbol")[
        "raw_dollar_volume"
    ].transform(
        lambda values: values.shift(1).rolling(20, min_periods=20).median()
    )
    daily_output = args.output_dir / "daily_state.parquet"
    daily.drop(columns="_merge").to_parquet(daily_output, index=False)

    observed_days = minutes[["symbol", "date"]].drop_duplicates()
    expected_controls = pd.MultiIndex.from_product(
        [sessions, ["QQQ", "SMH"]], names=["date", "symbol"]
    ).to_frame(index=False)
    expected_stocks = membership[membership["date"].isin(sessions)]
    coverage = pd.concat(
        [expected_stocks[["date", "symbol"]], expected_controls],
        ignore_index=True,
    ).drop_duplicates()
    coverage = coverage.merge(
        observed_days,
        on=["date", "symbol"],
        how="left",
        indicator=True,
        validate="one_to_one",
    )
    missing = coverage[coverage["_merge"].ne("both")][
        ["date", "symbol"]
    ].sort_values(["symbol", "date"])
    missing_output = args.output_dir / "missing_symbol_days.parquet"
    missing.to_parquet(missing_output, index=False)

    counts = (
        minutes.groupby("symbol")
        .agg(
            rows=("minute", "size"),
            observed_sessions=("date", "nunique"),
            minimum_date=("date", "min"),
            maximum_date=("date", "max"),
        )
        .reset_index()
    )
    expected_counts = coverage.groupby("symbol").size().rename(
        "expected_sessions"
    )
    missing_counts = missing.groupby("symbol").size().rename(
        "missing_sessions"
    )
    counts = (
        counts.merge(expected_counts, on="symbol", how="outer")
        .merge(missing_counts, on="symbol", how="left")
        .fillna({"missing_sessions": 0})
        .sort_values("symbol")
    )
    report = {
        "status": "passed",
        "minute_source": "local catalog bars_1m SIP raw",
        "minute_symbols_available": list(LOCAL_MINUTE_SYMBOLS),
        "minute_symbol_declared_but_unavailable": ["SOXX"],
        "minute_rows": int(len(minutes)),
        "point_in_time_membership_rows": int(len(membership)),
        "expected_symbol_days": int(len(coverage)),
        "covered_symbol_days": int(coverage["_merge"].eq("both").sum()),
        "missing_symbol_days": int(len(missing)),
        "coverage_by_symbol": counts.where(pd.notna(counts), None).to_dict(
            "records"
        ),
        "daily_rows": int(len(daily)),
        "daily_symbols": int(daily["symbol"].nunique()),
        "daily_split_only_unavailable_symbols": ["SOXX"],
        "maximum_loaded_date": str(
            max(minutes["date"].max(), daily["date"].max()).date()
        ),
        "holdout_rows_loaded": int(
            minutes["date"].ge(HOLDOUT_START).sum()
            + daily["date"].ge(HOLDOUT_START).sum()
        ),
        "invalid_minute_ohlc_rows": int(bad_minute.sum()),
        "hashes": {
            "minute_regular": sha256(minute_output),
            "membership": sha256(membership_output),
            "daily_state": sha256(daily_output),
            "missing_symbol_days": sha256(missing_output),
            "daily_split_input": sha256(args.daily_split),
        },
    }
    (args.output_dir / "report.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
