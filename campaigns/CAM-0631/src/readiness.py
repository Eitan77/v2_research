from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN = ROOT / "campaigns" / "CAM-0631"
OUT = CAMPAIGN / "artifacts" / "RUN-0001"
ENV = ROOT / ".env.local"
CUTOFF = pd.Timestamp("2026-04-30 23:59:59.999999999", tz="America/New_York")
START = pd.Timestamp("2026-04-30 10:00:00", tz="America/New_York")
END = pd.Timestamp("2026-04-30 10:01:00", tz="America/New_York")
SYMBOL = "AAPL"


def iso_z(value: pd.Timestamp) -> str:
    return value.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def fetch_all(session: requests.Session, path: str) -> tuple[list[dict], int]:
    params = {
        "symbols": SYMBOL,
        "start": iso_z(START),
        "end": iso_z(END),
        "feed": "sip",
        "limit": 10_000,
        "sort": "asc",
    }
    rows: list[dict] = []
    pages = 0
    seen: set[str] = set()
    while True:
        response = session.get(f"https://data.alpaca.markets{path}", params=params, timeout=45)
        response.raise_for_status()
        payload = response.json()
        pages += 1
        key = "quotes" if path.endswith("quotes") else "trades"
        values = payload.get(key, {}).get(SYMBOL, [])
        if not isinstance(values, list):
            raise RuntimeError(f"unexpected {key} payload")
        rows.extend(values)
        token = payload.get("next_page_token")
        if not token:
            break
        if token in seen:
            raise RuntimeError(f"repeated pagination token for {key}")
        seen.add(token)
        params["page_token"] = token
    return rows, pages


def timestamp_audit(rows: list[dict]) -> dict:
    timestamps = pd.to_datetime([row.get("t") for row in rows], utc=True, errors="coerce", format="mixed")
    if len(timestamps) == 0 or timestamps.isna().any():
        raise RuntimeError("empty or invalid timestamps")
    start_utc = START.tz_convert("UTC")
    end_utc = END.tz_convert("UTC")
    if timestamps.min() < start_utc or timestamps.max() > end_utc:
        raise RuntimeError("provider returned rows outside requested window")
    if timestamps.max().tz_convert("America/New_York") > CUTOFF:
        raise RuntimeError("holdout row detected")
    return {
        "row_count": len(rows),
        "minimum_timestamp": timestamps.min().isoformat(),
        "maximum_timestamp": timestamps.max().isoformat(),
        "monotone_non_decreasing": bool(timestamps.is_monotonic_increasing),
    }


def main() -> None:
    if END.tz_convert("America/New_York") > CUTOFF:
        raise RuntimeError("request crosses discovery cutoff")
    load_dotenv(ENV)
    key = os.getenv("ALPACA_API_KEY_ID", "")
    secret = os.getenv("ALPACA_API_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError("Alpaca credentials missing")
    session = requests.Session()
    session.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"})
    quotes, quote_pages = fetch_all(session, "/v2/stocks/quotes")
    trades, trade_pages = fetch_all(session, "/v2/stocks/trades")
    quote_audit = timestamp_audit(quotes)
    trade_audit = timestamp_audit(trades)
    valid_quotes = [row for row in quotes if float(row.get("bp") or 0) > 0 and float(row.get("ap") or 0) >= float(row.get("bp") or 0)]
    valid_trades = [row for row in trades if float(row.get("p") or 0) > 0 and float(row.get("s") or 0) > 0]
    if not valid_quotes or not valid_trades:
        raise RuntimeError("no valid SIP quote/trade rows")
    report = {
        "status": "passed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "request": {"symbol": SYMBOL, "start": iso_z(START), "end": iso_z(END), "feed": "sip"},
        "cutoff": "2026-04-30",
        "holdout_rows_loaded": 0,
        "credentials_present_not_persisted": True,
        "raw_payload_persisted": False,
        "quotes": {**quote_audit, "pages": quote_pages, "valid_nbbo_rows": len(valid_quotes), "schema_keys": sorted(set().union(*(row.keys() for row in quotes)))},
        "trades": {**trade_audit, "pages": trade_pages, "valid_print_rows": len(valid_trades), "schema_keys": sorted(set().union(*(row.keys() for row in trades)))},
        "gate": "signal_study_allowed_fill_simulation_still_blocked",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "readiness_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
