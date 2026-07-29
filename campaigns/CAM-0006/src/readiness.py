from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cam0006 import (
    CUTOFF,
    HOLDOUT_START,
    is_probable_split_ratio,
    select_official_open,
)


START = pd.Timestamp("2024-07-01")
DAILY_START = pd.Timestamp("2024-05-01")
EXPECTED_FULL_DAY_MINUTES = 386  # 09:30 through 15:55 inclusive


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_auctions(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected_rows: list[dict] = []
    status_rows: list[dict] = []
    for item in frame.itertuples():
        try:
            records = json.loads(item.o)
        except (TypeError, json.JSONDecodeError):
            records = []
        selected, status = select_official_open(records)
        status_rows.append({"symbol": item.symbol, "date": item.date, "status": status})
        if selected is None:
            continue
        timestamp = pd.to_datetime(selected["timestamp"], utc=True)
        local = timestamp.tz_convert("America/New_York")
        selected_rows.append(
            {
                "symbol": item.symbol,
                "date": pd.Timestamp(item.date),
                "auction_price_raw": selected["price"],
                "auction_size": selected["size"],
                "auction_exchange": selected["exchange"],
                "auction_timestamp": timestamp,
                "auction_local_time": local.strftime("%H:%M:%S.%f"),
                "known_by_0931": local.time()
                < pd.Timestamp("09:31:00").time(),
                "auction_dollar_value": selected["price"] * selected["size"],
            }
        )
    return pd.DataFrame(selected_rows), pd.DataFrame(status_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = args.output_dir / "duckdb_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(str(args.catalog), read_only=True)
    connection.execute(f"SET temp_directory='{temp_dir.as_posix()}'")
    auction_query = """
        SELECT a.symbol, a.date, a.o, a.feed
        FROM auctions a
        INNER JOIN qqq_pit_membership_daily m
          ON a.symbol = m.symbol
         AND a.date = TRY_CAST(m.date AS DATE)
         AND m.is_member
        WHERE a.date BETWEEN DATE '2024-07-01' AND DATE '2026-04-30'
          AND a.feed = 'sip'
        ORDER BY a.date, a.symbol
    """
    raw_auctions = connection.execute(auction_query).fetchdf()
    if pd.to_datetime(raw_auctions["date"]).max() > CUTOFF:
        raise RuntimeError("Holdout auction row loaded")
    official, statuses = parse_auctions(raw_auctions)
    if official.empty:
        raise RuntimeError("No official auctions selected")
    market_calendar = connection.execute(
        """
        SELECT DISTINCT TRY_CAST(date AS DATE) AS date, open, close
        FROM calendar
        WHERE TRY_CAST(date AS DATE)
          BETWEEN DATE '2024-07-01' AND DATE '2026-04-30'
        ORDER BY date
        """
    ).fetchdf()
    market_calendar["date"] = pd.to_datetime(market_calendar["date"])
    if market_calendar.duplicated("date").any():
        raise RuntimeError("Conflicting calendar rows")
    close_minutes = pd.to_timedelta(
        market_calendar["close"].str.slice(0, 2).astype(int), unit="h"
    ) + pd.to_timedelta(
        market_calendar["close"].str.slice(3, 5).astype(int), unit="m"
    )
    market_calendar["liquidation_delta"] = close_minutes - pd.Timedelta(minutes=5)
    market_calendar["liquidation_minute"] = (
        pd.Timestamp("2000-01-01")
        + market_calendar["liquidation_delta"]
    ).dt.strftime("%H:%M")
    market_calendar["final_exit_minute"] = (
        pd.Timestamp("2000-01-01")
        + market_calendar["liquidation_delta"]
        - pd.Timedelta(minutes=1)
    ).dt.strftime("%H:%M")
    market_calendar["expected_minutes"] = (
        (
            market_calendar["liquidation_delta"]
            - pd.Timedelta(hours=9, minutes=30)
        )
        / pd.Timedelta(minutes=1)
        + 1
    ).astype(int)
    official = official.merge(
        market_calendar[
            [
                "date",
                "close",
                "liquidation_minute",
                "final_exit_minute",
                "expected_minutes",
            ]
        ],
        on="date",
        how="inner",
        validate="many_to_one",
    )
    official.to_parquet(args.output_dir / "official_auctions.parquet", index=False)
    statuses.to_parquet(args.output_dir / "auction_statuses.parquet", index=False)

    symbols = sorted(official["symbol"].unique().tolist())
    connection.register("campaign_symbols", pd.DataFrame({"symbol": symbols}))
    daily = connection.execute(
        """
        SELECT b.symbol, b.date, b.open, b.high, b.low, b.close, b.volume,
               b.feed, b.adjustment
        FROM bars_1d b
        INNER JOIN campaign_symbols s USING(symbol)
        WHERE b.date BETWEEN DATE '2024-05-01' AND DATE '2026-04-30'
          AND b.feed = 'sip' AND b.adjustment = 'raw'
        ORDER BY b.symbol, b.date
        """
    ).fetchdf()
    if pd.to_datetime(daily["date"]).max() > CUTOFF:
        raise RuntimeError("Holdout daily row loaded")
    if daily.duplicated(["symbol", "date"]).any():
        raise RuntimeError("Duplicate raw daily rows")
    daily["date"] = pd.to_datetime(daily["date"])
    daily["prior_close_raw"] = daily.groupby("symbol")["close"].shift(1)
    daily["dollar_volume"] = daily["close"] * daily["volume"]
    daily["prior_dollar_volume"] = daily.groupby("symbol")[
        "dollar_volume"
    ].transform(
        lambda series: series.shift(1).rolling(20, min_periods=20).median()
    )
    daily.to_parquet(args.output_dir / "daily_raw.parquet", index=False)

    keys = official[
        [
            "symbol",
            "date",
            "liquidation_minute",
            "final_exit_minute",
            "expected_minutes",
        ]
    ].drop_duplicates()
    connection.register("event_keys", keys)
    minute_path = (args.output_dir / "regular_minutes.parquet").as_posix()
    if (args.output_dir / "regular_minutes.parquet").exists():
        (args.output_dir / "regular_minutes.parquet").unlink()
    connection.execute(
        f"""
        COPY (
          SELECT b.symbol, b.date,
                 TRY_CAST(b.timestamp AS TIMESTAMPTZ) AS timestamp,
                 TRY_CAST(b.timestamp AS TIMESTAMPTZ)
                   AT TIME ZONE 'America/New_York' AS local_ts,
                 STRFTIME(
                   TRY_CAST(b.timestamp AS TIMESTAMPTZ)
                     AT TIME ZONE 'America/New_York', '%H:%M'
                 ) AS minute,
                 b.open, b.high, b.low, b.close, b.volume, b.trade_count,
                 b.vwap, b.feed, b.adjustment, k.liquidation_minute,
                 k.final_exit_minute, k.expected_minutes
          FROM bars_1m b
          INNER JOIN event_keys k ON b.symbol=k.symbol AND b.date=k.date
          WHERE b.date BETWEEN DATE '2024-07-01' AND DATE '2026-04-30'
            AND b.feed='sip' AND b.adjustment='raw'
            AND STRFTIME(
              TRY_CAST(b.timestamp AS TIMESTAMPTZ)
                AT TIME ZONE 'America/New_York', '%H:%M'
            ) BETWEEN '09:30' AND k.liquidation_minute
          ORDER BY b.date, b.symbol, timestamp
        ) TO '{minute_path}' (
          FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000
        )
        """
    )
    minute_relation = f"READ_PARQUET('{minute_path}')"
    coverage = connection.execute(
        f"""
        SELECT symbol, date,
               COUNT(*) AS minute_rows,
               COUNT(DISTINCT timestamp) AS distinct_minutes,
               COUNT(*) FILTER (WHERE minute='09:30') AS n_0930,
               COUNT(*) FILTER (WHERE minute='09:31') AS n_0931,
               COUNT(*) FILTER (WHERE minute='09:44') AS n_0944,
               COUNT(*) FILTER (WHERE minute='09:59') AS n_0959,
               COUNT(*) FILTER (WHERE minute='11:59') AS n_1159,
               COUNT(*) FILTER (WHERE minute='15:54') AS n_1554,
               COUNT(*) FILTER (
                 WHERE minute=final_exit_minute
               ) AS n_final_exit,
               MAX(expected_minutes) AS expected_minutes,
               MAX(liquidation_minute) AS liquidation_minute,
               MAX(final_exit_minute) AS final_exit_minute,
               MIN(timestamp) AS first_timestamp,
               MAX(timestamp) AS last_timestamp
        FROM {minute_relation}
        GROUP BY symbol, date
        ORDER BY date, symbol
        """
    ).fetchdf()
    coverage["date"] = pd.to_datetime(coverage["date"])
    coverage["complete_path"] = (
        coverage["minute_rows"].eq(coverage["expected_minutes"])
        & coverage["distinct_minutes"].eq(coverage["expected_minutes"])
        & coverage[["n_0930", "n_0931", "n_0944", "n_0959", "n_1159"]]
        .eq(1)
        .all(axis=1)
        & (
            coverage["n_1554"].eq(1)
            | coverage["liquidation_minute"].lt("15:54")
        )
    )
    coverage.to_parquet(args.output_dir / "minute_coverage.parquet", index=False)

    events = official.merge(
        daily[
            [
                "symbol",
                "date",
                "prior_close_raw",
                "prior_dollar_volume",
            ]
        ],
        on=["symbol", "date"],
        how="left",
        validate="one_to_one",
    ).merge(
        coverage[
            [
                "symbol",
                "date",
                "minute_rows",
                "distinct_minutes",
                "complete_path",
                "n_0930",
                "n_0931",
                "n_0944",
                "n_0959",
                "n_1159",
                "n_final_exit",
            ]
        ],
        on=["symbol", "date"],
        how="left",
        validate="one_to_one",
    )
    events["raw_gap"] = events["auction_price_raw"] / events["prior_close_raw"] - 1
    events["probable_split"] = [
        is_probable_split_ratio(current, prior)
        for current, prior in zip(
            events["auction_price_raw"], events["prior_close_raw"], strict=True
        )
    ]
    events["corporate_action_safe"] = ~events["probable_split"]
    events["signal_complete"] = (
        events["known_by_0931"]
        & events["prior_close_raw"].gt(0)
        & events["prior_dollar_volume"].gt(0)
        & events["n_0930"].eq(1)
        & events["n_0931"].eq(1)
        & events["corporate_action_safe"]
    )
    events["all_exit_marks_complete"] = (
        events[["n_0944", "n_0959", "n_1159", "n_final_exit"]]
        .eq(1)
        .all(axis=1)
    )
    events["bar_model_complete"] = (
        events["signal_complete"] & events["all_exit_marks_complete"]
    )
    events.to_parquet(args.output_dir / "event_readiness.parquet", index=False)

    status_counts = (
        statuses["status"].value_counts(dropna=False).sort_index().to_dict()
    )
    report = {
        "status": "passed",
        "plan_hash_excluding_file_hash_line": "679e435ccb26c990090e87c63f57ca0969c4b91b671b9337f18f60cf41f01261",
        "raw_auction_membership_rows": int(len(raw_auctions)),
        "auction_status_counts": {str(k): int(v) for k, v in status_counts.items()},
        "selected_official_auctions_on_valid_sessions": int(len(official)),
        "selected_symbols": int(official["symbol"].nunique()),
        "known_by_0931": int(official["known_by_0931"].sum()),
        "complete_minute_paths": int(coverage["complete_path"].sum()),
        "signal_complete_events": int(events["signal_complete"].sum()),
        "bar_model_complete_events": int(events["bar_model_complete"].sum()),
        "dense_trade_minute_paths": int(events["complete_path"].sum()),
        "probable_split_rejections": int(events["probable_split"].sum()),
        "minimum_date": str(events["date"].min().date()),
        "maximum_loaded_date": str(events["date"].max().date()),
        "holdout_rows_loaded": int(events["date"].ge(HOLDOUT_START).sum()),
        "full_day_expected_minutes": EXPECTED_FULL_DAY_MINUTES,
        "early_close_sessions": int(
            market_calendar["liquidation_minute"].lt("15:55").sum()
        ),
        "hashes": {
            "official_auctions": sha256(args.output_dir / "official_auctions.parquet"),
            "auction_statuses": sha256(args.output_dir / "auction_statuses.parquet"),
            "daily_raw": sha256(args.output_dir / "daily_raw.parquet"),
            "regular_minutes": sha256(args.output_dir / "regular_minutes.parquet"),
            "minute_coverage": sha256(args.output_dir / "minute_coverage.parquet"),
            "event_readiness": sha256(args.output_dir / "event_readiness.parquet"),
        },
    }
    if report["holdout_rows_loaded"] != 0:
        raise RuntimeError("Holdout row loaded")
    (args.output_dir / "readiness.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
