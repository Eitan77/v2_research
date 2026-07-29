from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cam0007 import CUTOFF, session_offset


DAILY_START = pd.Timestamp("2024-05-01")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--event-registry", type=Path, required=True)
    parser.add_argument("--daily-split", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = args.output_dir / "duckdb_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    registry = pd.read_parquet(args.event_registry)
    registry["entry_session"] = pd.to_datetime(registry["entry_session"])
    if registry["entry_session"].max() > CUTOFF:
        raise RuntimeError("Registry crosses sealed entry boundary")
    symbols = sorted(registry["symbol"].unique().tolist())
    symbol_frame = pd.DataFrame({"symbol": symbols})

    con = duckdb.connect(str(args.catalog), read_only=True)
    con.execute(f"SET temp_directory='{temp_dir.as_posix()}'")
    con.register("registry_frame", registry[["symbol", "entry_session"]])
    con.register("symbol_frame", symbol_frame)
    try:
        calendar = con.execute(
            """
            SELECT DISTINCT try_cast(date AS DATE) AS date, open, close
            FROM calendar
            WHERE try_cast(date AS DATE)
                  BETWEEN DATE '2024-07-01' AND DATE '2026-04-30'
            ORDER BY date
            """
        ).fetch_df()
        # The catalog view spans thousands of daily parquet partitions. On
        # Windows, one broad join can exhaust file handles before partition
        # pruning completes. Read only the exact event-date partitions,
        # serially, and filter to that day's registry symbols.
        market_root = (
            args.catalog.parent
            / "raw"
            / "alpaca"
            / "market"
            / "stocks"
            / "bars_1m"
            / "feed=sip"
        )
        minute_frames = []
        for entry_session, date_registry in registry.groupby("entry_session"):
            date = pd.Timestamp(entry_session)
            date_glob = (
                market_root
                / f"year={date.year:04d}"
                / f"month={date.month:02d}"
                / f"date={date:%Y-%m-%d}"
                / "*.parquet"
            )
            if not date_glob.parent.exists():
                continue
            day_symbols = pd.DataFrame(
                {"symbol": sorted(date_registry["symbol"].unique())}
            )
            con.register("day_symbols", day_symbols)
            minute_frames.append(
                con.execute(
                    """
                    SELECT b.symbol, try_cast(b.date AS DATE) AS date,
                           b.timestamp, b.open, b.high, b.low, b.close,
                           b.volume, b.trade_count, b.vwap, b.feed,
                           b.adjustment
                    FROM read_parquet(?, hive_partitioning=true) b
                    INNER JOIN day_symbols s USING (symbol)
                    WHERE b.feed = 'sip'
                      AND b.adjustment = 'raw'
                      AND b.timeframe = '1Min'
                    ORDER BY b.symbol, b.timestamp
                    """,
                    [date_glob.as_posix()],
                ).fetch_df()
            )
            con.unregister("day_symbols")
        minutes = (
            pd.concat(minute_frames, ignore_index=True)
            if minute_frames
            else pd.DataFrame()
        )
        daily_raw = con.execute(
            """
            SELECT b.symbol, b.date, b.open, b.high, b.low, b.close, b.volume,
                   b.feed, b.adjustment
            FROM bars_1d b
            INNER JOIN symbol_frame s USING (symbol)
            WHERE b.feed = 'sip'
              AND b.adjustment = 'raw'
              AND b.timeframe = '1Day'
              AND b.date BETWEEN DATE '2024-05-01' AND DATE '2026-04-30'
            ORDER BY b.symbol, b.date, b.adjustment
            """
        ).fetch_df()
    finally:
        con.close()
    calendar["date"] = pd.to_datetime(calendar["date"])
    sessions = pd.DatetimeIndex(calendar["date"])
    calendar["close_minutes"] = (
        calendar["close"].str.slice(0, 2).astype(int) * 60
        + calendar["close"].str.slice(3, 5).astype(int)
    )
    calendar["final_exit_minute"] = (
        pd.Timestamp("2000-01-01")
        + pd.to_timedelta(calendar["close_minutes"] - 6, unit="m")
    ).dt.strftime("%H:%M")

    minutes["date"] = pd.to_datetime(minutes["date"])
    minutes["timestamp"] = pd.to_datetime(
        minutes["timestamp"], format="mixed", utc=True
    )
    minutes["local_ts"] = minutes["timestamp"].dt.tz_convert(
        "America/New_York"
    )
    minutes["minute"] = minutes["local_ts"].dt.strftime("%H:%M")
    minutes = minutes.merge(
        calendar[["date", "final_exit_minute"]],
        on="date",
        how="left",
        validate="many_to_one",
    )
    minutes = minutes[
        minutes["minute"].ge("09:30")
        & minutes["minute"].le(minutes["final_exit_minute"])
    ].copy()
    if minutes.duplicated(["symbol", "date", "minute"]).any():
        raise RuntimeError("Duplicate event minute")

    daily_split = pd.read_parquet(args.daily_split)[
        [
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "feed",
            "adjustment",
        ]
    ].copy()
    daily = pd.concat([daily_raw, daily_split], ignore_index=True)
    daily["date"] = pd.to_datetime(daily["date"])
    if daily["date"].max() > CUTOFF:
        raise RuntimeError("Targeted split daily input crosses holdout boundary")
    if daily.duplicated(["symbol", "date", "adjustment"]).any():
        raise RuntimeError("Duplicate daily price row")
    raw = daily[daily["adjustment"].eq("raw")].drop(
        columns=["adjustment"]
    )
    split = daily[daily["adjustment"].eq("split")].drop(
        columns=["adjustment"]
    )
    joined = raw.merge(
        split,
        on=["symbol", "date", "feed"],
        how="outer",
        suffixes=("_raw", "_split"),
        indicator=True,
        validate="one_to_one",
    )
    joined["split_factor"] = joined["close_split"] / joined["close_raw"]
    joined["dollar_volume_raw"] = (
        joined["close_raw"] * joined["volume_raw"]
    )
    joined = joined.sort_values(["symbol", "date"])
    joined["prior_close_split"] = joined.groupby("symbol")[
        "close_split"
    ].shift(1)
    joined["prior20_median_dollar_volume"] = joined.groupby("symbol")[
        "dollar_volume_raw"
    ].transform(lambda x: x.shift(1).rolling(20, min_periods=20).median())
    if joined["date"].max() > CUTOFF or minutes["date"].max() > CUTOFF:
        raise RuntimeError("Market readiness loaded sealed row")

    daily_lookup = joined.set_index(["symbol", "date"])
    minute_groups = {
        (symbol, pd.Timestamp(date)): group.sort_values("minute")
        for (symbol, date), group in minutes.groupby(["symbol", "date"])
    }
    rows = []
    for event in registry.itertuples(index=False):
        date = pd.Timestamp(event.entry_session)
        key = (event.symbol, date)
        day = daily_lookup.loc[key] if key in daily_lookup.index else None
        path = minute_groups.get(key)
        row = {**event._asdict()}
        if day is None or path is None:
            row.update(
                {
                    "market_day_available": False,
                    "signal_complete": False,
                    "same_day_complete": False,
                }
            )
            rows.append(row)
            continue
        exact = {
            minute: path[path["minute"].eq(minute)]
            for minute in ("09:30", "09:59", "10:00")
        }
        final_minute = str(path["final_exit_minute"].iloc[0])
        final = path[path["minute"].eq(final_minute)]
        factor = float(day["split_factor"])
        signal_complete = (
            all(len(exact[minute]) == 1 for minute in exact)
            and np.isfinite(factor)
            and factor > 0
            and np.isfinite(day["prior_close_split"])
            and np.isfinite(day["prior20_median_dollar_volume"])
        )
        same_day_complete = signal_complete and len(final) == 1
        row.update(
            {
                "market_day_available": True,
                "split_factor": factor,
                "prior_close_split": float(day["prior_close_split"]),
                "prior20_median_dollar_volume": float(
                    day["prior20_median_dollar_volume"]
                ),
                "final_exit_minute": final_minute,
                "observed_minute_rows": int(len(path)),
                "signal_complete": bool(signal_complete),
                "same_day_complete": bool(same_day_complete),
            }
        )
        if signal_complete:
            first_window = path[
                path["minute"].between("09:30", "09:59")
            ]
            row.update(
                {
                    "open_0930_raw": float(exact["09:30"].iloc[0]["open"]),
                    "close_0959_raw": float(exact["09:59"].iloc[0]["close"]),
                    "entry_1000_raw": float(exact["10:00"].iloc[0]["open"]),
                    "first30_volume": float(first_window["volume"].sum()),
                    "first30_trade_count": float(
                        first_window["trade_count"].sum()
                    ),
                    "first30_dollar_volume": float(
                        (first_window["volume"] * first_window["vwap"]).sum()
                    ),
                    "path_high_1000_to_final_raw": float(
                        path[
                            path["minute"].between("10:00", final_minute)
                        ]["high"].max()
                    ),
                    "gap_return": float(
                        exact["09:30"].iloc[0]["open"]
                        * factor
                        / day["prior_close_split"]
                        - 1.0
                    ),
                    "first30_return": float(
                        exact["09:59"].iloc[0]["close"]
                        / exact["09:30"].iloc[0]["open"]
                        - 1.0
                    ),
                    "entry_1000_split": float(
                        exact["10:00"].iloc[0]["open"] * factor
                    ),
                }
            )
        if same_day_complete:
            row["exit_final_split"] = float(final.iloc[0]["open"] * factor)
        targets = {
            "next_open": session_offset(date, sessions, 1),
            "three_close": session_offset(date, sessions, 2),
            "five_close": session_offset(date, sessions, 4),
            "ten_close": session_offset(date, sessions, 9),
        }
        for label, target in targets.items():
            row[f"{label}_session"] = target
            if target is None or target > CUTOFF:
                row[f"exit_{label}_split"] = np.nan
                continue
            target_key = (event.symbol, pd.Timestamp(target))
            if target_key not in daily_lookup.index:
                row[f"exit_{label}_split"] = np.nan
                continue
            target_day = daily_lookup.loc[target_key]
            column = "open_split" if label == "next_open" else "close_split"
            row[f"exit_{label}_split"] = float(target_day[column])
        rows.append(row)
    readiness = pd.DataFrame(rows)
    signal_ready = readiness[readiness["signal_complete"]].copy()
    if signal_ready.empty:
        raise RuntimeError("No signal-ready earnings events")
    signal_ready["gap_rank"] = signal_ready.groupby("entry_session")[
        "gap_return"
    ].rank(pct=True, method="average")
    signal_ready["abs_gap_rank"] = signal_ready.groupby("entry_session")[
        "gap_return"
    ].transform(lambda x: x.abs().rank(pct=True, method="average"))
    signal_ready["first30_dollar_participation"] = (
        signal_ready["first30_dollar_volume"]
        / signal_ready["prior20_median_dollar_volume"]
    )
    signal_ready["first30_volume_rank"] = signal_ready.groupby(
        "entry_session"
    )["first30_dollar_participation"].rank(pct=True, method="average")
    readiness = readiness.drop(
        columns=[
            column
            for column in (
                "gap_rank",
                "abs_gap_rank",
                "first30_dollar_participation",
                "first30_volume_rank",
            )
            if column in readiness
        ]
    ).merge(
        signal_ready[
            [
                "symbol",
                "entry_session",
                "gap_rank",
                "abs_gap_rank",
                "first30_dollar_participation",
                "first30_volume_rank",
            ]
        ],
        on=["symbol", "entry_session"],
        how="left",
        validate="one_to_one",
    )

    raw.to_parquet(args.output_dir / "daily_raw.parquet", index=False)
    split.to_parquet(args.output_dir / "daily_split.parquet", index=False)
    minutes.to_parquet(args.output_dir / "event_minutes.parquet", index=False)
    readiness.to_parquet(args.output_dir / "event_readiness.parquet", index=False)
    report = {
        "status": "passed",
        "registry_events": int(len(registry)),
        "registry_symbols": int(registry["symbol"].nunique()),
        "daily_raw_rows": int(len(raw)),
        "daily_split_rows": int(len(split)),
        "daily_join_both_rows": int(joined["_merge"].eq("both").sum()),
        "daily_raw_only_rows": int(joined["_merge"].eq("left_only").sum()),
        "daily_split_only_rows": int(joined["_merge"].eq("right_only").sum()),
        "event_minute_rows": int(len(minutes)),
        "market_day_available_events": int(
            readiness["market_day_available"].sum()
        ),
        "signal_complete_events": int(readiness["signal_complete"].sum()),
        "same_day_complete_events": int(
            readiness["same_day_complete"].sum()
        ),
        "next_open_complete_events": int(
            readiness["exit_next_open_split"].notna().sum()
        ),
        "three_close_complete_events": int(
            readiness["exit_three_close_split"].notna().sum()
        ),
        "five_close_complete_events": int(
            readiness["exit_five_close_split"].notna().sum()
        ),
        "ten_close_complete_events": int(
            readiness["exit_ten_close_split"].notna().sum()
        ),
        "liquidity_eligible_signal_events": int(
            (
                readiness["signal_complete"]
                & readiness["prior20_median_dollar_volume"].ge(100_000_000)
            ).sum()
        ),
        "minimum_entry_session": str(
            readiness["entry_session"].min().date()
        ),
        "maximum_entry_session": str(
            readiness["entry_session"].max().date()
        ),
        "maximum_loaded_date": str(
            max(daily["date"].max(), minutes["date"].max()).date()
        ),
        "holdout_rows_loaded": 0,
        "multiday_attrition_policy": (
            "Targets after 2026-04-30 remain missing; no holdout row is loaded."
        ),
    }
    (args.output_dir / "market_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    hashes = {
        path.name: sha256(path)
        for path in args.output_dir.iterdir()
        if path.is_file()
    }
    (args.output_dir / "hashes.json").write_text(
        json.dumps(hashes, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
