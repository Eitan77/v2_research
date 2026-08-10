from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CATALOG = Path(r"D:\AlgoResearch\data\catalog.duckdb")
QUOTE_DB = Path(r"D:\AlgoResearch\data\raw\alpaca\market\stocks\quotes_sip\schema_v1\quote_lake.duckdb")
OUT = ROOT / "campaigns" / "CAM-0606" / "artifacts" / "RUN-0022" / "execution_data_readiness.json"

with duckdb.connect(str(CATALOG), read_only=True) as con:
    bars = con.execute("""
        SELECT symbol, min(date) minimum_date, max(date) maximum_date,
               count(*) row_count, count(DISTINCT date) date_count,
               count(DISTINCT feed) feeds, count(DISTINCT adjustment) adjustments
        FROM bars_1m
        WHERE symbol IN ('SMH','XLK') AND date <= DATE '2026-04-30'
        GROUP BY symbol ORDER BY symbol
    """).df()
    sample = con.execute("""
        SELECT symbol,timestamp,open,high,low,close,date,feed,adjustment
        FROM bars_1m WHERE symbol='SMH' AND date=DATE '2025-05-13'
        ORDER BY timestamp LIMIT 3
    """).df()
with duckdb.connect(str(QUOTE_DB), read_only=True) as con:
    quotes = con.execute("""
        SELECT symbol, min(quote_ts) minimum_ts, max(quote_ts) maximum_ts,
               count(*) row_count, count(DISTINCT CAST(quote_ts AS DATE)) date_count
        FROM sip_quotes
        WHERE symbol IN ('SMH','XLK') AND quote_ts < TIMESTAMPTZ '2026-05-01 00:00:00+00'
        GROUP BY symbol ORDER BY symbol
    """).df()
payload = {
    "bars": bars.to_dict("records"),
    "bar_sample": sample.astype(str).to_dict("records"),
    "quotes": quotes.astype(str).to_dict("records"),
    "maximum_allowed_date": "2026-04-30",
    "holdout_rows_loaded": 0,
}
OUT.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, default=str))
