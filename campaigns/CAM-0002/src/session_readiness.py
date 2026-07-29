from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import exchange_calendars as xcals
import pandas as pd


ROOT = Path(r"D:\AlgoResearch\data\raw\alpaca\market\stocks\bars_1m\feed=sip")
CUTOFF = pd.Timestamp("2026-04-30")
WARMUP_START = pd.Timestamp("2024-08-01")
EVALUATION_START = pd.Timestamp("2024-11-01")


def globs() -> list[str]:
    months = pd.period_range(WARMUP_START, CUTOFF, freq="M")
    return [
        str(
            ROOT
            / f"year={p.year}"
            / f"month={p.month:02d}"
            / "date=*"
            / "*.parquet"
        ).replace("\\", "/")
        for p in months
    ]


def schedule() -> pd.DataFrame:
    cal = xcals.get_calendar("XNYS")
    sessions = cal.sessions_in_range(WARMUP_START, CUTOFF)
    rows = []
    for session in sessions:
        open_time = cal.session_open(session)
        close_time = cal.session_close(session)
        rows.append(
            {
                "date": session.tz_localize(None),
                "market_open": open_time,
                "market_close": close_time,
                "expected_minutes": int((close_time - open_time).total_seconds() // 60),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    temp = args.output_dir / "duckdb_tmp"
    temp.mkdir(exist_ok=True)
    sched = schedule()
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{str(temp).replace(chr(92), '/')}'")
    con.register("schedule", sched)
    paths = globs()
    query = """
    WITH ranked AS (
      SELECT b.*,
        row_number() OVER (
          PARTITION BY b.symbol,b.timestamp,b.timeframe,b.feed,b.adjustment
          ORDER BY coalesce(try_cast(b.ingested_at AS TIMESTAMP),TIMESTAMP '1900-01-01') DESC,
                   coalesce(b.source_ingestion_id,'') DESC
        ) rn
      FROM read_parquet(?,union_by_name=true,hive_partitioning=true) b
      WHERE b.date BETWEEN DATE '2024-08-01' AND DATE '2026-04-30'
        AND b.feed='sip' AND b.adjustment='raw'
    ), bars AS (
      SELECT symbol,date,try_cast(timestamp AS TIMESTAMPTZ) ts,open,high,low,close,volume,trade_count
      FROM ranked WHERE rn=1
    ), regular AS (
      SELECT b.*,s.expected_minutes
      FROM bars b JOIN schedule s USING(date)
      WHERE b.ts >= s.market_open AND b.ts < s.market_close
    )
    SELECT symbol,date,max(expected_minutes) expected_minutes,
           count(DISTINCT ts) observed_minutes,
           sum(volume) volume,sum(trade_count) trade_count,
           min(ts) first_bar,max(ts) last_bar,
           min(open) min_open,max(open) max_open
    FROM regular
    GROUP BY symbol,date
    ORDER BY date,symbol
    """
    coverage = con.execute(query, [paths]).fetchdf()
    con.close()
    coverage["date"] = pd.to_datetime(coverage["date"])
    symbols = sorted(coverage["symbol"].unique())
    grid = pd.MultiIndex.from_product(
        [symbols, sched["date"]], names=["symbol", "date"]
    ).to_frame(index=False)
    grid = grid.merge(
        sched[["date", "expected_minutes"]], on="date", how="left"
    )
    coverage = grid.merge(
        coverage.drop(columns=["expected_minutes"]),
        on=["symbol", "date"],
        how="left",
        validate="one_to_one",
    )
    for column in ["observed_minutes", "volume", "trade_count"]:
        coverage[column] = coverage[column].fillna(0)
    coverage["coverage_fraction"] = (
        coverage["observed_minutes"] / coverage["expected_minutes"]
    )
    coverage = coverage.sort_values(["symbol", "date"])
    rolling_observed = (
        coverage.groupby("symbol")["observed_minutes"]
        .rolling(60, min_periods=60)
        .sum()
        .reset_index(level=0, drop=True)
    )
    rolling_expected = (
        coverage.groupby("symbol")["expected_minutes"]
        .rolling(60, min_periods=60)
        .sum()
        .reset_index(level=0, drop=True)
    )
    coverage["prior60_trade_minute_fraction"] = (
        rolling_observed / rolling_expected
    ).groupby(coverage["symbol"]).shift(1)
    coverage["eligible_liquid"] = coverage["prior60_trade_minute_fraction"] >= 0.90
    coverage.to_parquet(args.output_dir / "daily_coverage.parquet", index=False)
    eval_rows = coverage[coverage["date"] >= EVALUATION_START]
    per_date = eval_rows.groupby("date").agg(
        symbols=("symbol", "nunique"),
        eligible_symbols=("eligible_liquid", "sum"),
    )
    report = {
        "status": "passed",
        "calendar": "XNYS via exchange_calendars 4.13.2",
        "regular_session_rule": "[market_open, market_close), including early closes",
        "warmup_start": str(WARMUP_START.date()),
        "evaluation_start": str(EVALUATION_START.date()),
        "max_loaded_date": str(coverage["date"].max().date()),
        "holdout_rows_loaded": int((coverage["date"] >= "2026-05-01").sum()),
        "calendar_sessions": int(sched.shape[0]),
        "coverage_symbol_dates": int(coverage.shape[0]),
        "union_symbols": int(coverage["symbol"].nunique()),
        "evaluation_sessions": int(per_date.shape[0]),
        "eligible_symbols_per_date": {
            "min": int(per_date["eligible_symbols"].min()),
            "median": float(per_date["eligible_symbols"].median()),
            "max": int(per_date["eligible_symbols"].max()),
        },
        "zero_eligible_dates": int((per_date["eligible_symbols"] == 0).sum()),
        "early_close_sessions": int((sched["expected_minutes"] < 390).sum()),
    }
    if report["max_loaded_date"] > "2026-04-30" or report["holdout_rows_loaded"]:
        raise RuntimeError("holdout validation failed")
    (args.output_dir / "session_readiness.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
