from __future__ import annotations

import json
from pathlib import Path

import duckdb


CATALOG = Path(r"D:\AlgoResearch\data\catalog.duckdb")
QUOTE_DB = Path(r"D:\AlgoResearch\data\raw\alpaca\market\stocks\quotes_sip\schema_v1\quote_lake.duckdb")
OUTPUT = Path(__file__).resolve().parents[1] / "artifacts" / "shared" / "intraday_source_inventory.json"


def main() -> None:
    with duckdb.connect(str(CATALOG), read_only=True) as con:
        bar_tables = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE lower(table_name) LIKE '%bar%' ORDER BY 1"
        ).fetchall()
        bar_stats = con.execute(
            """
            SELECT min(timestamp) AS minimum_timestamp,
                   max(timestamp) AS maximum_timestamp,
                   count(*) AS rows,
                   count(DISTINCT symbol) AS symbols,
                   sum(CASE WHEN timestamp >= TIMESTAMPTZ '2026-05-01 00:00:00+00' THEN 1 ELSE 0 END) AS holdout_rows
            FROM derived_bars_5m
            WHERE timestamp < TIMESTAMPTZ '2026-05-01 00:00:00+00'
            """
        ).fetchone()
        bar_schemas = {
            table: con.execute(f"DESCRIBE SELECT * FROM {table}").df().to_dict(orient="records")
            for table in ("derived_bars_5m", "derived_bars_15m")
        }
    quote_report = {"path": str(QUOTE_DB), "exists": QUOTE_DB.exists()}
    if QUOTE_DB.exists():
        with duckdb.connect(str(QUOTE_DB), read_only=True) as con:
            tables = [x[0] for x in con.execute("SHOW TABLES").fetchall()]
            quote_report["tables"] = tables
            quote_report["schemas"] = {
                table: con.execute(f"DESCRIBE SELECT * FROM {table}").df().to_dict(orient="records")
                for table in tables
            }
            quote_report["table_counts"] = {
                table: int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in tables
            }
            quote_report["quote_span"] = con.execute(
                "SELECT min(session_date), max(session_date), count(DISTINCT symbol), "
                "sum(CASE WHEN session_date >= DATE '2026-05-01' THEN 1 ELSE 0 END) FROM sip_quotes"
            ).fetchone()
            quote_report["coverage_span"] = con.execute(
                "SELECT min(session_date), max(session_date), count(DISTINCT symbol), "
                "sum(CASE WHEN session_date >= DATE '2026-05-01' THEN 1 ELSE 0 END) FROM sip_quote_coverage"
            ).fetchone()
            quote_report["qqq_quote_span"] = con.execute(
                "SELECT min(quote_ts), max(quote_ts), count(*) FROM sip_quotes WHERE symbol='QQQ'"
            ).fetchone()
            quote_report["qqq_samples"] = con.execute(
                "SELECT symbol, quote_ts, bid_price, ask_price FROM sip_quotes WHERE symbol='QQQ' ORDER BY quote_ts LIMIT 5"
            ).fetchall()
            quote_report["quote_symbols"] = [x[0] for x in con.execute(
                "SELECT DISTINCT symbol FROM sip_quotes ORDER BY symbol"
            ).fetchall()]
            quote_report["aapl_samples"] = con.execute(
                "SELECT symbol, quote_ts, session_date, bid_price, ask_price FROM sip_quotes WHERE symbol='AAPL' ORDER BY quote_ts LIMIT 5"
            ).fetchall()
    report = {
        "catalog": str(CATALOG),
        "bar_tables": [x[0] for x in bar_tables],
        "derived_bars_5m": {
            "minimum_timestamp": str(bar_stats[0]),
            "maximum_timestamp": str(bar_stats[1]),
            "rows": int(bar_stats[2]),
            "symbols": int(bar_stats[3]),
            "holdout_rows_loaded": int(bar_stats[4] or 0),
        },
        "bar_schemas": bar_schemas,
        "quote_lake": quote_report,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
