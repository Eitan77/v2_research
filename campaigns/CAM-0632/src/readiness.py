from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import duckdb
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN = ROOT / "campaigns" / "CAM-0632"
OUT = CAMPAIGN / "artifacts" / "RUN-0001"
CATALOG = Path(r"D:\AlgoResearch\data\catalog.duckdb")
CUTOFF = "2026-04-30"
SYMBOLS = ["SMH", "SOXL", "SOXS", "QQQ", "TQQQ", "SQQQ", "QLD", "QID", "SPY", "SPXL", "SPXS"]
PAIRS = [("SMH", "SOXL", "SOXS"), ("QQQ", "TQQQ", "SQQQ"), ("QQQ", "QLD", "QID"), ("SPY", "SPXL", "SPXS")]


def main() -> None:
    if not CATALOG.exists():
        raise RuntimeError(f"missing catalog {CATALOG}")
    OUT.mkdir(parents=True, exist_ok=True)
    temp = (ROOT / "tmp" / "duckdb_cam0632").resolve()
    temp.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(CATALOG), read_only=True)
    con.execute(f"set temp_directory='{temp.as_posix()}'")
    con.execute("set threads=16")
    schema = con.execute("describe bars_1m").fetchdf()
    required = {"date", "symbol", "timestamp", "open", "high", "low", "close", "volume", "feed", "adjustment", "ingested_at"}
    missing = sorted(required - set(schema.column_name))
    if missing:
        raise RuntimeError(f"bars_1m missing columns {missing}")
    quoted = ",".join("?" for _ in SYMBOLS)
    query = f"""
        select date, symbol, try_cast(timestamp as timestamptz) as ts,
               arg_max(open, try_cast(ingested_at as timestamp)) as open,
               arg_max(high, try_cast(ingested_at as timestamp)) as high,
               arg_max(low, try_cast(ingested_at as timestamp)) as low,
               arg_max(close, try_cast(ingested_at as timestamp)) as close,
               arg_max(volume, try_cast(ingested_at as timestamp)) as volume
        from bars_1m
        where date <= date '{CUTOFF}'
          and feed = 'sip' and adjustment = 'raw'
          and symbol in ({quoted})
          and strftime(try_cast(timestamp as timestamptz) at time zone 'America/New_York','%H:%M') between '09:30' and '15:59'
        group by 1,2,3
    """
    bars = con.execute(query, SYMBOLS).fetchdf()
    con.close()
    if bars.empty:
        raise RuntimeError("no bars returned")
    bars["date"] = pd.to_datetime(bars.date)
    bars["ts"] = pd.to_datetime(bars.ts, utc=True)
    if bars.date.max() > pd.Timestamp(CUTOFF):
        raise RuntimeError("holdout row loaded")
    if bars.duplicated(["date", "symbol", "ts"]).any():
        raise RuntimeError("duplicate deduplicated keys remain")
    if (bars[["open", "high", "low", "close"]] <= 0).any().any():
        raise RuntimeError("nonpositive OHLC")
    bad_ohlc = (bars.high < bars[["open", "close", "low"]].max(axis=1)) | (bars.low > bars[["open", "close", "high"]].min(axis=1))
    if bad_ohlc.any():
        raise RuntimeError("invalid OHLC envelope")
    coverage_rows = []
    sessions_by_symbol: dict[str, set] = {}
    for symbol, group in bars.groupby("symbol"):
        counts = group.groupby("date").size()
        complete = set(counts[counts >= 300].index)
        sessions_by_symbol[symbol] = complete
        coverage_rows.append({
            "symbol": symbol,
            "first_date": group.date.min().date().isoformat(),
            "last_date": group.date.max().date().isoformat(),
            "rows": len(group),
            "sessions": int(group.date.nunique()),
            "sessions_ge_300_bars": len(complete),
        })
    pair_rows = []
    for underlying, bull, inverse in PAIRS:
        missing_symbols = [symbol for symbol in (underlying, bull, inverse) if symbol not in sessions_by_symbol]
        complete = set.intersection(*(sessions_by_symbol.get(symbol, set()) for symbol in (underlying, bull, inverse))) if not missing_symbols else set()
        pair_rows.append({"underlying": underlying, "bull": bull, "inverse": inverse, "complete_sessions": len(complete), "missing_symbols": missing_symbols})
    if any(row["complete_sessions"] == 0 for row in pair_rows):
        failure = {
            "status": "failed",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "catalog": str(CATALOG),
            "query_cutoff_literal": CUTOFF,
            "maximum_loaded_date": bars.date.max().date().isoformat(),
            "holdout_rows_loaded": 0,
            "deduplicated_rows": len(bars),
            "pair_coverage": pair_rows,
            "failure": "two declared starting pairs have no catalog bars",
            "gate": "bar_stage_blocked",
        }
        pd.DataFrame(coverage_rows).sort_values("symbol").to_csv(OUT / "symbol_coverage.csv", index=False)
        pd.DataFrame(pair_rows).to_csv(OUT / "pair_coverage.csv", index=False)
        (OUT / "readiness_report.json").write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
        raise RuntimeError(f"starting pair has no complete sessions: {pair_rows}")
    # Start-stamp fixture: a 09:30 row covers the first minute and may only decide after 09:31.
    sample = bars.sort_values("ts").groupby(["date", "symbol"], sort=False).head(2)
    deltas = sample.sort_values("ts").groupby(["date", "symbol"]).ts.diff().dropna().dt.total_seconds()
    if deltas.empty or not (deltas == 60).all():
        raise RuntimeError("one-minute timestamp adjacency fixture failed")
    pd.DataFrame(coverage_rows).sort_values("symbol").to_csv(OUT / "symbol_coverage.csv", index=False)
    pd.DataFrame(pair_rows).to_csv(OUT / "pair_coverage.csv", index=False)
    report = {
        "status": "passed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "catalog": str(CATALOG),
        "query_cutoff_literal": CUTOFF,
        "maximum_loaded_date": bars.date.max().date().isoformat(),
        "holdout_rows_loaded": 0,
        "deduplicated_rows": len(bars),
        "duplicate_keys_after_dedup": 0,
        "timestamp_contract": "start_stamped_decide_after_bar_close_enter_next_actionable_bar",
        "pair_coverage": pair_rows,
        "gate": "bar_stage_allowed_quote_replay_blocked_until_survivor",
    }
    (OUT / "readiness_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
