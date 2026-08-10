from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import pandas as pd


CAMPAIGN = Path(__file__).resolve().parents[1]
SHARED = CAMPAIGN / "artifacts" / "shared"
QUOTE_DB = Path(r"D:\AlgoResearch\data\raw\alpaca\market\stocks\quotes_sip\schema_v1\quote_lake.duckdb")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--roles", type=Path, default=SHARED / "quote_roles.parquet")
    parser.add_argument("--output", type=Path, default=SHARED / "local_quote_role_matches.parquet")
    parser.add_argument("--missing-output", type=Path, default=SHARED / "missing_quote_roles.parquet")
    parser.add_argument("--window-minutes", type=int, default=5)
    args = parser.parse_args()
    roles = args.roles
    output = args.output
    missing_output = args.missing_output
    with duckdb.connect() as con:
        con.execute("SET threads=16")
        con.execute(f"ATTACH '{QUOTE_DB.as_posix()}' AS qlake (READ_ONLY)")
        con.execute(f"CREATE TEMP TABLE roles AS SELECT * FROM read_parquet('{roles.as_posix()}')")
        after = con.execute(f"""
            SELECT r.symbol, r.target_ts, r.role,
                   q.quote_ts, q.bid_price, q.ask_price, q.bid_size, q.ask_size,
                   q.feed, q.provider
            FROM (SELECT * FROM roles WHERE role IN ('entry_ask_after','exit_bid_after')) r
            LEFT JOIN qlake.sip_quotes q
              ON r.symbol=q.symbol
             AND q.quote_ts >= r.target_ts
             AND q.quote_ts <= r.target_ts + INTERVAL {args.window_minutes} MINUTE
             AND q.quote_ts < TIMESTAMPTZ '2026-05-01 00:00:00+00'
            QUALIFY row_number() OVER (
              PARTITION BY r.symbol, r.target_ts, r.role ORDER BY q.quote_ts ASC NULLS LAST
            ) = 1
        """).df()
        before = con.execute(f"""
            SELECT r.symbol, r.target_ts, r.role,
                   q.quote_ts, q.bid_price, q.ask_price, q.bid_size, q.ask_size,
                   q.feed, q.provider
            FROM (SELECT * FROM roles WHERE role='exit_bid_before') r
            LEFT JOIN qlake.sip_quotes q
              ON r.symbol=q.symbol
             AND q.quote_ts <= r.target_ts
             AND q.quote_ts >= r.target_ts - INTERVAL {args.window_minutes} MINUTE
             AND q.quote_ts < TIMESTAMPTZ '2026-05-01 00:00:00+00'
            QUALIFY row_number() OVER (
              PARTITION BY r.symbol, r.target_ts, r.role ORDER BY q.quote_ts DESC NULLS LAST
            ) = 1
        """).df()
    matched = pd.concat([after, before], ignore_index=True)
    valid = (
        matched["quote_ts"].notna()
        & matched["bid_price"].notna() & matched["ask_price"].notna()
        & (matched["bid_price"] > 0) & (matched["ask_price"] >= matched["bid_price"])
    )
    matched["match_valid"] = valid
    all_roles = pd.read_parquet(roles)
    merged = all_roles.merge(
        matched, on=["symbol", "target_ts", "role"], how="left", validate="one_to_one"
    )
    merged["match_valid"] = merged["match_valid"].fillna(False).astype(bool)
    merged.to_parquet(output, index=False)
    missing = merged[~merged["match_valid"]][["symbol", "target_ts", "role"]].copy()
    missing.to_parquet(missing_output, index=False)
    report = {
        "status": "passed", "source": str(QUOTE_DB), "source_read_only": True,
        "roles": int(len(merged)), "matched_roles": int(merged["match_valid"].sum()),
        "missing_roles": int((~merged["match_valid"]).sum()),
        "coverage_rate": float(merged["match_valid"].mean()),
        "symbols_requested": int(merged["symbol"].nunique()),
        "symbols_matched": int(merged.loc[merged["match_valid"], "symbol"].nunique()),
        "symbols_missing": int(missing["symbol"].nunique()),
        "holdout_rows_loaded": int((pd.to_datetime(merged.loc[merged["match_valid"], "quote_ts"], utc=True) >= pd.Timestamp("2026-05-01", tz="UTC")).sum()),
        "matching_window_minutes": args.window_minutes,
    }
    output.with_name(output.stem + "_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
