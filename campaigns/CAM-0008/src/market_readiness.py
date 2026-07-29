from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cam0008 import CUTOFF


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def offset_session(
    date: pd.Timestamp, sessions: pd.DatetimeIndex, offset: int
) -> pd.Timestamp | None:
    position = np.flatnonzero(sessions == pd.Timestamp(date))
    if len(position) != 1 or int(position[0]) + offset >= len(sessions):
        return None
    return pd.Timestamp(sessions[int(position[0]) + offset])


def prior_minute(clock: str) -> str:
    value = pd.Timestamp(f"2000-01-01 {clock}") - pd.Timedelta(minutes=1)
    return value.strftime("%H:%M")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--event-registry", type=Path, required=True)
    parser.add_argument("--daily-split", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    registry = pd.read_parquet(args.event_registry)
    registry["entry_session"] = pd.to_datetime(registry["entry_session"])
    if registry["entry_session"].max() > CUTOFF:
        raise RuntimeError("Event registry crosses sealed boundary")
    symbols = sorted(registry["symbol"].unique())
    con = duckdb.connect(str(args.catalog), read_only=True)
    temp_dir = args.output_dir / "duckdb_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory='{temp_dir.as_posix()}'")
    con.register("symbol_frame", pd.DataFrame({"symbol": symbols}))
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
        daily_raw = con.execute(
            """
            SELECT b.symbol, b.date, b.open, b.high, b.low, b.close, b.volume,
                   b.feed, b.adjustment
            FROM bars_1d b
            INNER JOIN symbol_frame s USING(symbol)
            WHERE b.feed='sip' AND b.adjustment='raw'
              AND b.timeframe='1Day'
              AND b.date BETWEEN DATE '2024-05-01' AND DATE '2026-04-30'
            ORDER BY b.symbol, b.date
            """
        ).fetch_df()
        root = (
            args.catalog.parent
            / "raw"
            / "alpaca"
            / "market"
            / "stocks"
            / "bars_1m"
            / "feed=sip"
        )
        minute_frames = []
        for date, group in registry.groupby("entry_session"):
            date = pd.Timestamp(date)
            glob = (
                root
                / f"year={date.year:04d}"
                / f"month={date.month:02d}"
                / f"date={date:%Y-%m-%d}"
                / "*.parquet"
            )
            if not glob.parent.exists():
                continue
            day_symbols = pd.DataFrame(
                {"symbol": sorted(group["symbol"].unique())}
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
                    INNER JOIN day_symbols s USING(symbol)
                    WHERE b.feed='sip' AND b.adjustment='raw'
                      AND b.timeframe='1Min'
                    ORDER BY b.symbol, b.timestamp
                    """,
                    [glob.as_posix()],
                ).fetch_df()
            )
            con.unregister("day_symbols")
    finally:
        con.close()
    minutes = pd.concat(minute_frames, ignore_index=True)
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
    ]
    daily = pd.concat([daily_raw, daily_split], ignore_index=True)
    daily["date"] = pd.to_datetime(daily["date"])
    if daily["date"].max() > CUTOFF:
        raise RuntimeError("Daily input crosses sealed boundary")
    if daily.duplicated(["symbol", "date", "adjustment"]).any():
        raise RuntimeError("Duplicate daily adjustment key")
    raw = daily[daily["adjustment"].eq("raw")].drop(columns="adjustment")
    split = daily[daily["adjustment"].eq("split")].drop(columns="adjustment")
    joined = raw.merge(
        split,
        on=["symbol", "date", "feed"],
        how="outer",
        suffixes=("_raw", "_split"),
        indicator=True,
        validate="one_to_one",
    )
    joined["split_factor"] = joined["close_split"] / joined["close_raw"]
    joined["dollar_volume_raw"] = joined["close_raw"] * joined["volume_raw"]
    joined = joined.sort_values(["symbol", "date"])
    joined["prior_close_split"] = joined.groupby("symbol")[
        "close_split"
    ].shift(1)
    joined["prior20_median_dollar_volume"] = joined.groupby("symbol")[
        "dollar_volume_raw"
    ].transform(lambda x: x.shift(1).rolling(20, min_periods=20).median())

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
    minutes["timestamp"] = pd.to_datetime(minutes["timestamp"], utc=True)
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
        raise RuntimeError("Duplicate minute key")
    if minutes["date"].max() > CUTOFF:
        raise RuntimeError("Minute input crosses sealed boundary")
    minute_groups = {
        (symbol, pd.Timestamp(date)): group.sort_values("minute")
        for (symbol, date), group in minutes.groupby(["symbol", "date"])
    }
    daily_lookup = joined.set_index(["symbol", "date"])
    rows = []
    for event in registry.itertuples(index=False):
        date = pd.Timestamp(event.entry_session)
        key = (event.symbol, date)
        row = {**event._asdict()}
        path = minute_groups.get(key)
        day = daily_lookup.loc[key] if key in daily_lookup.index else None
        if path is None or day is None:
            row.update(
                {
                    "market_day_available": False,
                    "signal_complete": False,
                    "same_day_complete": False,
                }
            )
            rows.append(row)
            continue
        entry_minute = str(event.entry_minute)
        reaction_start = str(event.reaction_start_minute)
        reaction_end = prior_minute(entry_minute)
        reaction = path[
            path["minute"].between(reaction_start, reaction_end)
        ]
        entry = path[path["minute"].eq(entry_minute)]
        final_minute = str(path["final_exit_minute"].iloc[0])
        final = path[path["minute"].eq(final_minute)]
        open_bar = path[path["minute"].eq("09:30")]
        factor = float(day["split_factor"])
        signal_complete = (
            len(reaction) == 5
            and len(entry) == 1
            and len(open_bar) == 1
            and np.isfinite(factor)
            and factor > 0
            and np.isfinite(day["prior_close_split"])
            and np.isfinite(day["prior20_median_dollar_volume"])
            and entry_minute < final_minute
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
                "reaction_observed_rows": int(len(reaction)),
                "signal_complete": bool(signal_complete),
                "same_day_complete": bool(same_day_complete),
            }
        )
        if signal_complete:
            reaction_open = float(reaction.iloc[0]["open"])
            reaction_close = float(reaction.iloc[-1]["close"])
            reaction_high = float(reaction["high"].max())
            reaction_low = float(reaction["low"].min())
            reaction_range = reaction_high - reaction_low
            entry_raw = float(entry.iloc[0]["open"])
            row.update(
                {
                    "event_day_open_raw": float(open_bar.iloc[0]["open"]),
                    "reaction_open_raw": reaction_open,
                    "reaction_close_raw": reaction_close,
                    "reaction_high_raw": reaction_high,
                    "reaction_low_raw": reaction_low,
                    "reaction_return": reaction_close / reaction_open - 1,
                    "action_aligned_reaction": (
                        reaction_close / reaction_open - 1
                    )
                    * int(event.action_sign),
                    "reaction_close_location": (
                        (reaction_close - reaction_low) / reaction_range
                        if reaction_range > 0
                        else np.nan
                    ),
                    "reaction_volume": float(reaction["volume"].sum()),
                    "reaction_trade_count": float(
                        reaction["trade_count"].sum()
                    ),
                    "reaction_dollar_volume": float(
                        (reaction["volume"] * reaction["vwap"]).sum()
                    ),
                    "reaction_dollar_participation": float(
                        (reaction["volume"] * reaction["vwap"]).sum()
                        / day["prior20_median_dollar_volume"]
                    ),
                    "gap_return": float(
                        open_bar.iloc[0]["open"]
                        * factor
                        / day["prior_close_split"]
                        - 1
                    ),
                    "pre_reaction_return": float(
                        reaction_open / open_bar.iloc[0]["open"] - 1
                    ),
                    "entry_raw": entry_raw,
                    "entry_split": entry_raw * factor,
                    "path_high_entry_to_final_raw": float(
                        path[
                            path["minute"].between(
                                entry_minute, final_minute
                            )
                        ]["high"].max()
                    ),
                }
            )
        if same_day_complete:
            row["exit_final_split"] = float(final.iloc[0]["open"] * factor)
        targets = {
            "next_open": (1, "open_split"),
            "two_close": (1, "close_split"),
            "three_close": (2, "close_split"),
            "five_close": (4, "close_split"),
            "ten_close": (9, "close_split"),
        }
        for label, (offset, column) in targets.items():
            target = offset_session(date, sessions, offset)
            row[f"{label}_session"] = target
            target_key = (event.symbol, target) if target is not None else None
            if (
                target is None
                or target > CUTOFF
                or target_key not in daily_lookup.index
            ):
                row[f"exit_{label}_split"] = np.nan
            else:
                row[f"exit_{label}_split"] = float(
                    daily_lookup.loc[target_key][column]
                )
        rows.append(row)
    readiness = pd.DataFrame(rows)
    output = args.output_dir / "event_readiness.parquet"
    raw.to_parquet(args.output_dir / "daily_raw.parquet", index=False)
    split.to_parquet(args.output_dir / "daily_split.parquet", index=False)
    minutes.to_parquet(args.output_dir / "event_minutes.parquet", index=False)
    readiness.to_parquet(output, index=False)
    report = {
        "status": "passed",
        "registry_events": int(len(registry)),
        "registry_symbols": int(registry["symbol"].nunique()),
        "unique_event_symbol_days": int(
            registry[["symbol", "entry_session"]].drop_duplicates().shape[0]
        ),
        "daily_raw_rows": int(len(raw)),
        "daily_split_rows": int(len(split)),
        "daily_join_both_rows": int(joined["_merge"].eq("both").sum()),
        "daily_raw_only_rows": int(joined["_merge"].eq("left_only").sum()),
        "event_minute_rows": int(len(minutes)),
        "market_day_available_events": int(
            readiness["market_day_available"].sum()
        ),
        "signal_complete_events": int(readiness["signal_complete"].sum()),
        "same_day_complete_events": int(
            readiness["same_day_complete"].sum()
        ),
        "liquidity_eligible_signal_events": int(
            (
                readiness["signal_complete"]
                & readiness["prior20_median_dollar_volume"].ge(100_000_000)
            ).sum()
        ),
        "horizon_complete": {
            label: int(readiness[f"exit_{label}_split"].notna().sum())
            for label in (
                "next_open",
                "two_close",
                "three_close",
                "five_close",
                "ten_close",
            )
        },
        "minimum_entry_session": str(readiness["entry_session"].min().date()),
        "maximum_entry_session": str(readiness["entry_session"].max().date()),
        "maximum_loaded_date": str(
            max(minutes["date"].max(), daily["date"].max()).date()
        ),
        "holdout_rows_loaded": 0,
        "multiday_attrition_policy": "Targets after April 30 remain missing; sealed rows are never loaded."
    }
    (args.output_dir / "market_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    hashes = {
        path.name: sha256(path)
        for path in (
            output,
            args.output_dir / "daily_raw.parquet",
            args.output_dir / "daily_split.parquet",
            args.output_dir / "event_minutes.parquet",
            args.output_dir / "market_report.json",
        )
    }
    (args.output_dir / "hashes.json").write_text(
        json.dumps(hashes, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
