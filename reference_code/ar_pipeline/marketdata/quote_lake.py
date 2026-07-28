"""Canonical, reusable storage for normalized quote windows.

The lake is one logical DuckDB table backed by one compact Parquet partition
per session.  Coverage windows are stored separately so an empty quote result
is distinguishable from a window that was never downloaded.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import duckdb
import numpy as np
import pandas as pd


DEFAULT_ROOT = Path("D:/AlgoResearch/data/raw/alpaca/market/stocks/quotes_sip/schema_v1")
QUOTE_KEY = ["symbol", "quote_ts", "bid_price", "ask_price", "bid_size", "ask_size"]
COVERAGE_KEY = ["symbol", "window_start_ts", "window_end_ts", "feed"]


def _session_from_path(path: Path) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    if not match:
        raise ValueError(f"Cannot determine session date from {path}")
    return match.group(1)


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.parquet")
    frame.to_parquet(temporary, index=False, compression="zstd")
    temporary.replace(path)


def _normalize_quotes(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "quote_ts", "bid_price", "ask_price", "bid_size", "ask_size"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"Quote input missing columns: {missing}")
    result = frame[list(required)].copy()
    result["symbol"] = result.symbol.astype(str).str.upper()
    result["quote_ts"] = pd.to_datetime(result.quote_ts, utc=True)
    for column in ("bid_price", "ask_price", "bid_size", "ask_size"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result[
        result.symbol.ne("")
        & result.quote_ts.notna()
        & result.bid_price.gt(0)
        & result.ask_price.gt(0)
        & result.ask_price.ge(result.bid_price)
        & result.bid_size.ge(0)
        & result.ask_size.ge(0)
    ]
    result["feed"] = "sip"
    result["provider"] = "alpaca"
    return result.drop_duplicates(QUOTE_KEY).sort_values(["symbol", "quote_ts", "bid_price", "ask_price"])


def publish_run(run_root: str | Path, lake_root: str | Path = DEFAULT_ROOT) -> dict:
    run_root = Path(run_root)
    lake_root = Path(lake_root)
    daily_files = sorted((run_root / "daily_quotes").glob("quotes_*.parquet"))
    if not daily_files:
        raise FileNotFoundError(f"No daily quote files under {run_root / 'daily_quotes'}")
    endpoint_path = run_root / "quote_endpoints.parquet"
    if not endpoint_path.exists():
        raise FileNotFoundError(endpoint_path)
    endpoints = pd.read_parquet(endpoint_path)
    endpoints["request_ts"] = pd.to_datetime(endpoints.request_ts, utc=True)
    if "window_start_ts" in endpoints:
        endpoints["window_start_ts"] = pd.to_datetime(endpoints.window_start_ts, utc=True)
    if "window_end_ts" in endpoints:
        endpoints["window_end_ts"] = pd.to_datetime(endpoints.window_end_ts, utc=True)
    endpoints["session_date"] = pd.to_datetime(endpoints.session_date).dt.strftime("%Y-%m-%d")
    source_run = run_root.name

    published_sessions = 0
    input_rows = 0
    added_rows = 0
    for source_path in daily_files:
        session = _session_from_path(source_path)
        incoming_raw = pd.read_parquet(source_path)
        input_rows += len(incoming_raw)
        incoming = _normalize_quotes(incoming_raw)
        partition = lake_root / f"session_date={session}"
        quote_path = partition / "quotes.parquet"
        old_count = 0
        if quote_path.exists():
            existing = pd.read_parquet(quote_path)
            old_count = len(existing)
            incoming = pd.concat([existing, incoming], ignore_index=True)
            incoming = incoming.drop_duplicates(QUOTE_KEY).sort_values(
                ["symbol", "quote_ts", "bid_price", "ask_price"]
            )
        _atomic_parquet(incoming, quote_path)
        added_rows += len(incoming) - old_count

        requested = endpoints[endpoints.session_date.eq(session)][["symbol", "request_ts"]].copy()
        if {"window_start_ts", "window_end_ts"}.issubset(endpoints.columns):
            requested = endpoints[endpoints.session_date.eq(session)][
                ["symbol", "window_start_ts", "window_end_ts"]
            ].copy()
        else:
            requested = endpoints[endpoints.session_date.eq(session)][["symbol", "request_ts"]].copy()
            requested.rename(columns={"request_ts": "window_start_ts"}, inplace=True)
            requested["window_end_ts"] = requested.window_start_ts + pd.Timedelta(seconds=10)
        requested["symbol"] = requested.symbol.astype(str).str.upper()
        requested["feed"] = "sip"
        requested["provider"] = "alpaca"
        requested["source_run"] = source_run
        requested["download_complete"] = True
        coverage_path = partition / "coverage.parquet"
        if coverage_path.exists():
            requested = pd.concat([pd.read_parquet(coverage_path), requested], ignore_index=True)
        requested = requested.drop_duplicates(COVERAGE_KEY, keep="last").sort_values(
            ["symbol", "window_start_ts"]
        )
        _atomic_parquet(requested, coverage_path)
        published_sessions += 1

    refresh_catalog(lake_root)
    audit = lake_audit(lake_root)
    audit.update({
        "source_run": source_run,
        "source_run_path": str(run_root),
        "published_sessions_this_call": published_sessions,
        "input_rows_this_call": input_rows,
        "new_unique_quote_rows_this_call": added_rows,
        "published_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    manifest_dir = lake_root / "ingest_manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{source_run}.json"
    manifest_path.write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
    return audit


def refresh_catalog(lake_root: str | Path = DEFAULT_ROOT) -> Path:
    lake_root = Path(lake_root)
    quote_files = list(lake_root.glob("session_date=*/quotes.parquet"))
    coverage_files = list(lake_root.glob("session_date=*/coverage.parquet"))
    if not quote_files or not coverage_files:
        raise FileNotFoundError("Quote lake requires at least one quote and coverage partition")
    catalog = lake_root / "quote_lake.duckdb"
    con = duckdb.connect(str(catalog))
    try:
        quote_glob = (lake_root / "session_date=*" / "quotes.parquet").as_posix()
        coverage_glob = (lake_root / "session_date=*" / "coverage.parquet").as_posix()
        con.execute(f"""
          CREATE OR REPLACE VIEW sip_quotes AS
          SELECT * FROM read_parquet('{quote_glob}', hive_partitioning=true, union_by_name=true)
        """)
        con.execute(f"""
          CREATE OR REPLACE VIEW sip_quote_coverage AS
          SELECT * FROM read_parquet('{coverage_glob}', hive_partitioning=true, union_by_name=true)
        """)
    finally:
        con.close()
    return catalog


def lake_audit(lake_root: str | Path = DEFAULT_ROOT) -> dict:
    lake_root = Path(lake_root)
    catalog = refresh_catalog(lake_root)
    con = duckdb.connect(str(catalog), read_only=True)
    try:
        quote = con.execute("""
          SELECT count(*) AS quote_rows, count(DISTINCT session_date) AS sessions,
                 count(DISTINCT symbol) AS symbols, min(quote_ts) AS first_quote_ts,
                 max(quote_ts) AS last_quote_ts FROM sip_quotes
        """).fetchdf().iloc[0].to_dict()
        coverage = con.execute("""
          SELECT count(*) AS windows, count(DISTINCT session_date) AS coverage_sessions,
                 count(DISTINCT symbol) AS coverage_symbols FROM sip_quote_coverage
        """).fetchdf().iloc[0].to_dict()
    finally:
        con.close()
    return {
        "schema_version": 1,
        "provider": "alpaca",
        "feed": "sip",
        "lake_root": str(lake_root),
        "logical_quote_table": "sip_quotes",
        "logical_coverage_table": "sip_quote_coverage",
        **quote,
        **coverage,
    }


def load_quote_windows(
    requests: pd.DataFrame,
    lake_root: str | Path = DEFAULT_ROOT,
    window_seconds: int = 10,
) -> pd.DataFrame | None:
    """Return normalized paths when every requested window is in the lake.

    ``None`` means at least one window has never been downloaded. An empty
    DataFrame means all windows are covered but no valid quotes were observed.
    """
    required = {"session_date", "symbol", "request_ts"}
    missing = sorted(required.difference(requests.columns))
    if missing:
        raise ValueError(f"Quote requests missing columns: {missing}")
    lake_root = Path(lake_root)
    catalog = lake_root / "quote_lake.duckdb"
    if not catalog.exists():
        return None
    req = requests[list(required)].drop_duplicates().copy()
    req["session_date"] = pd.to_datetime(req.session_date).dt.date
    req["symbol"] = req.symbol.astype(str).str.upper()
    req["request_ts"] = pd.to_datetime(req.request_ts, utc=True)
    req["window_end_ts"] = req.request_ts + pd.Timedelta(seconds=window_seconds)
    con = duckdb.connect(str(catalog), read_only=True)
    con.register("requested_windows", req)
    try:
        covered = con.execute("""
          SELECT count(*) FROM requested_windows r
          WHERE EXISTS (
            SELECT 1 FROM sip_quote_coverage c
            WHERE c.session_date = r.session_date AND c.symbol = r.symbol
              AND c.feed = 'sip' AND c.download_complete
              AND c.window_start_ts <= r.request_ts
              AND c.window_end_ts >= r.window_end_ts
          )
        """).fetchone()[0]
        if covered != len(req):
            return None
        result = con.execute("""
          SELECT r.request_ts, q.symbol, q.quote_ts, q.bid_price, q.ask_price,
                 q.bid_size, q.ask_size
          FROM requested_windows r
          JOIN sip_quotes q ON q.session_date = r.session_date AND q.symbol = r.symbol
            AND q.quote_ts >= r.request_ts AND q.quote_ts < r.window_end_ts
          ORDER BY r.request_ts,q.symbol,q.quote_ts
        """).fetchdf()
    finally:
        con.unregister("requested_windows")
        con.close()
    if result.empty:
        return pd.DataFrame({
            "request_ts": pd.Series(dtype="datetime64[ns, UTC]"),
            "symbol": pd.Series(dtype="str"),
            "quote_ts": pd.Series(dtype="datetime64[ns, UTC]"),
            "bid_price": pd.Series(dtype="float64"), "ask_price": pd.Series(dtype="float64"),
            "bid_size": pd.Series(dtype="float64"), "ask_size": pd.Series(dtype="float64"),
        })
    result["request_ts"] = pd.to_datetime(result.request_ts, utc=True)
    result["quote_ts"] = pd.to_datetime(result.quote_ts, utc=True)
    return result


def quote_window_coverage(
    requests: pd.DataFrame,
    lake_root: str | Path = DEFAULT_ROOT,
    window_seconds: int = 10,
) -> pd.Series:
    """Return a boolean Series aligned to requests for reusable lake coverage."""
    required = {"session_date", "symbol", "request_ts"}
    missing = sorted(required.difference(requests.columns))
    if missing:
        raise ValueError(f"Quote requests missing columns: {missing}")
    result = pd.Series(False, index=requests.index, dtype=bool)
    catalog = Path(lake_root) / "quote_lake.duckdb"
    if requests.empty or not catalog.exists():
        return result
    req = requests[list(required)].copy()
    req["request_row"] = np.arange(len(req), dtype="int64")
    req["session_date"] = pd.to_datetime(req.session_date).dt.date
    req["symbol"] = req.symbol.astype(str).str.upper()
    req["request_ts"] = pd.to_datetime(req.request_ts, utc=True)
    req["window_end_ts"] = req.request_ts + pd.Timedelta(seconds=window_seconds)
    con = duckdb.connect(str(catalog), read_only=True)
    con.register("requested_windows", req)
    try:
        covered_rows = con.execute("""
          SELECT r.request_row FROM requested_windows r
          WHERE EXISTS (
            SELECT 1 FROM sip_quote_coverage c
            WHERE c.session_date = r.session_date AND c.symbol = r.symbol
              AND c.feed = 'sip' AND c.download_complete
              AND c.window_start_ts <= r.request_ts
              AND c.window_end_ts >= r.window_end_ts
          )
        """).fetchnumpy()["request_row"]
    finally:
        con.unregister("requested_windows")
        con.close()
    if len(covered_rows):
        result.iloc[covered_rows] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("publish-run", "audit", "refresh-catalog"))
    parser.add_argument("--run-root")
    parser.add_argument("--lake-root", default=str(DEFAULT_ROOT))
    args = parser.parse_args()
    if args.command == "publish-run":
        if not args.run_root:
            parser.error("publish-run requires --run-root")
        result = publish_run(args.run_root, args.lake_root)
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "audit":
        print(json.dumps(lake_audit(args.lake_root), indent=2, default=str))
    else:
        print(refresh_catalog(args.lake_root))


if __name__ == "__main__":
    main()
