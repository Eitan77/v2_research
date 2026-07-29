from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb


ROOT = Path(r"D:\AlgoResearch\data\raw\alpaca\market\stocks\bars_1m\feed=sip")
MONTHS = [(2024, 11), (2024, 12)] + [(2025, m) for m in range(1, 13)] + [
    (2026, m) for m in range(1, 5)
]
CUTOFF = "2026-04-30"


def month_glob(year: int, month: int) -> str:
    return str(
        ROOT / f"year={year}" / f"month={month:02d}" / "date=*" / "*.parquet"
    ).replace("\\", "/")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.parent / "duckdb_tmp"
    temp.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{str(temp).replace(chr(92), '/')}'")
    monthly = []
    all_symbols: set[str] = set()
    for year, month in MONTHS:
        pattern = month_glob(year, month)
        if not list((ROOT / f"year={year}" / f"month={month:02d}").glob("date=*")):
            raise RuntimeError(f"missing declared month partition {year}-{month:02d}")
        query = """
        WITH ranked AS (
          SELECT *,
            row_number() OVER (
              PARTITION BY symbol, timestamp, timeframe, feed, adjustment
              ORDER BY coalesce(try_cast(ingested_at AS TIMESTAMP), TIMESTAMP '1900-01-01') DESC,
                       coalesce(source_ingestion_id, '') DESC
            ) AS rn
          FROM read_parquet(?, union_by_name=true, hive_partitioning=true)
          WHERE date <= DATE '2026-04-30'
            AND feed='sip' AND adjustment='raw'
        ), x AS (SELECT * EXCLUDE(rn) FROM ranked WHERE rn=1)
        SELECT count(*) AS n_rows, count(DISTINCT date) AS n_dates,
               count(DISTINCT symbol) AS n_symbols, min(date) AS min_date,
               max(date) AS max_date, min(timestamp) AS min_timestamp,
               max(timestamp) AS max_timestamp,
               sum(CASE WHEN open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL THEN 1 ELSE 0 END) AS null_ohlc,
               sum(CASE WHEN open<=0 OR high<=0 OR low<=0 OR close<=0 OR high<low THEN 1 ELSE 0 END) AS invalid_ohlc
        FROM x
        """
        row = con.execute(query, [pattern]).fetchone()
        symbols = {
            r[0]
            for r in con.execute(
                """
                WITH ranked AS (
                  SELECT symbol, row_number() OVER (
                    PARTITION BY symbol,timestamp,timeframe,feed,adjustment
                    ORDER BY coalesce(try_cast(ingested_at AS TIMESTAMP), TIMESTAMP '1900-01-01') DESC,
                             coalesce(source_ingestion_id,'') DESC
                  ) rn
                  FROM read_parquet(?,union_by_name=true,hive_partitioning=true)
                  WHERE date<=DATE '2026-04-30' AND feed='sip' AND adjustment='raw'
                ) SELECT DISTINCT symbol FROM ranked WHERE rn=1 ORDER BY symbol
                """,
                [pattern],
            ).fetchall()
        }
        all_symbols |= symbols
        monthly.append(
            dict(
                month=f"{year}-{month:02d}",
                rows=row[0],
                dates=row[1],
                symbols=row[2],
                min_date=str(row[3]),
                max_date=str(row[4]),
                min_timestamp=row[5],
                max_timestamp=row[6],
                null_ohlc=row[7],
                invalid_ohlc=row[8],
                symbol_set_sha256=hashlib.sha256(
                    "\n".join(sorted(symbols)).encode()
                ).hexdigest(),
            )
        )
    con.close()
    max_date = max(x["max_date"] for x in monthly)
    report = {
        "status": "passed",
        "source": str(ROOT),
        "months": monthly,
        "union_symbol_count": len(all_symbols),
        "max_loaded_date": max_date,
        "holdout_rows_loaded": 0,
        "cutoff": CUTOFF,
        "adjustment": "raw; intraday same-session returns",
        "failures": [],
    }
    if max_date > CUTOFF or any(x["null_ohlc"] or x["invalid_ohlc"] for x in monthly):
        raise RuntimeError("readiness validation failed")
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "months": len(monthly), "max_date": max_date,
                      "symbols": len(all_symbols), "rows": sum(x["rows"] for x in monthly)}))


if __name__ == "__main__":
    main()
