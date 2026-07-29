from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import exchange_calendars as xcals
import pandas as pd

from cam0003 import validate_cutoff


ROOT = Path(r"D:\AlgoResearch\data\raw\alpaca\market\stocks\bars_1m\feed=sip")
START = pd.Timestamp("2024-10-01")
CUTOFF = pd.Timestamp("2026-04-30")


def paths() -> list[str]:
    return [
        str(ROOT / f"year={p.year}" / f"month={p.month:02d}" / "date=*" / "*.parquet").replace("\\", "/")
        for p in pd.period_range(START, CUTOFF, freq="M")
    ]


def schedule() -> pd.DataFrame:
    cal = xcals.get_calendar("XNYS")
    rows = []
    for session in cal.sessions_in_range(START, CUTOFF):
        op, cl = cal.session_open(session), cal.session_close(session)
        rows.append({
            "date": session.tz_localize(None), "market_open": op, "market_close": cl,
            "expected_minutes": int((cl-op).total_seconds()//60),
        })
    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    temp = a.output_dir / "duckdb_tmp"
    temp.mkdir(exist_ok=True)
    sched = schedule()
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{str(temp).replace(chr(92), '/')}'")
    con.register("schedule", sched)
    query = """
    WITH ranked AS (
      SELECT b.*,
        row_number() OVER (
          PARTITION BY b.symbol,b.timestamp,b.timeframe,b.feed,b.adjustment
          ORDER BY coalesce(try_cast(b.ingested_at AS TIMESTAMP),TIMESTAMP '1900-01-01') DESC,
                   coalesce(b.source_ingestion_id,'') DESC
        ) rn
      FROM read_parquet(?,union_by_name=true,hive_partitioning=true) b
      WHERE b.date BETWEEN DATE '2024-10-01' AND DATE '2026-04-30'
        AND b.symbol='SPY' AND b.feed='sip' AND b.adjustment='raw'
    ), raw AS (
      SELECT date,try_cast(timestamp AS TIMESTAMPTZ) ts,open,close,volume,trade_count
      FROM ranked WHERE rn=1
    ), grid AS (
      SELECT s.*,r.i AS minute_index,
             s.market_open+r.i*INTERVAL 1 MINUTE AS ts
      FROM schedule s,range(0,s.expected_minutes) r(i)
    ), joined AS (
      SELECT g.*,b.open AS raw_open,b.close AS raw_close,
             b.volume AS raw_volume,b.trade_count AS raw_trade_count
      FROM grid g LEFT JOIN raw b USING(date,ts)
    ), filled AS (
      SELECT *,
        last_value(raw_close IGNORE NULLS) OVER (
          PARTITION BY date ORDER BY minute_index
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS price
      FROM joined
    )
    SELECT date,max(expected_minutes) AS expected_minutes,
      count(raw_close) AS observed_minutes,
      sum(coalesce(raw_volume,0)) AS session_volume,
      sum(coalesce(raw_trade_count,0)) AS session_trades,
      max(CASE WHEN minute_index=29 THEN price END) AS close_0959,
      max(CASE WHEN minute_index=29 THEN raw_close END) AS raw_close_0959,
      max(CASE WHEN minute_index=expected_minutes-30 THEN coalesce(raw_open,price) END) AS entry_1530,
      max(CASE WHEN minute_index=expected_minutes-30 THEN raw_open END) AS raw_entry_1530,
      max(CASE WHEN minute_index=expected_minutes-1 THEN price END) AS exit_1559,
      max(CASE WHEN minute_index=expected_minutes-1 THEN raw_close END) AS raw_exit_1559,
      sum(CASE WHEN minute_index<30 THEN coalesce(raw_volume,0) ELSE 0 END) AS first30_volume
    FROM filled GROUP BY date ORDER BY date
    """
    daily = con.execute(query, [paths()]).fetchdf()
    con.close()
    daily["date"] = pd.to_datetime(daily["date"])
    validate_cutoff(daily)
    daily["previous_close"] = daily["exit_1559"].shift(1)
    daily.to_parquet(a.output_dir / "spy_daily_readiness.parquet", index=False)
    eval_ = daily[daily["date"] >= "2024-11-01"]
    report = {
        "raw_paths": paths(), "symbol": "SPY", "adjustment": "raw", "feed": "sip",
        "calendar": "XNYS", "calendar_version": xcals.__version__,
        "sessions_loaded": len(daily), "evaluation_sessions": len(eval_),
        "early_close_sessions": int((daily["expected_minutes"] < 390).sum()),
        "observed_minutes_min": int(daily["observed_minutes"].min()),
        "observed_minutes_median": float(daily["observed_minutes"].median()),
        "expected_minutes_min": int(daily["expected_minutes"].min()),
        "key_field_nulls_evaluation": {
            c: int(eval_[c].isna().sum()) for c in
            ["previous_close", "close_0959", "entry_1530", "exit_1559"]
        },
        "exact_raw_key_missing_evaluation": {
            c: int(eval_[c].isna().sum()) for c in
            ["raw_close_0959", "raw_entry_1530", "raw_exit_1559"]
        },
        "min_session_trades": int(daily["session_trades"].min()),
        "loaded_max_date": str(daily["date"].max().date()),
        "holdout_rows_loaded": int((daily["date"] >= "2026-05-01").sum()),
    }
    if report["holdout_rows_loaded"] or any(report["key_field_nulls_evaluation"].values()):
        raise RuntimeError("readiness gate failed")
    (a.output_dir / "readiness.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
