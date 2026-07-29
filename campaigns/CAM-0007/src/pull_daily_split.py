from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import pandas as pd
import requests

from cam0007 import CUTOFF


HOLDOUT_START = pd.Timestamp("2026-05-01")


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    required = ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY")
    if any(not values.get(key) for key in required):
        raise RuntimeError("Local Alpaca credentials are incomplete")
    return values


def pull(
    session: requests.Session,
    base_url: str,
    symbols: list[str],
    start: str,
    end_exclusive: str,
) -> list[dict]:
    rows: list[dict] = []
    token = None
    while True:
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": f"{start}T00:00:00Z",
            "end": f"{end_exclusive}T00:00:00Z",
            "adjustment": "split",
            "feed": "sip",
            "sort": "asc",
            "limit": 10000,
        }
        if token:
            params["page_token"] = token
        for attempt in range(5):
            response = session.get(
                f"{base_url.rstrip('/')}/v2/stocks/bars",
                params=params,
                timeout=60,
            )
            if response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                break
            time.sleep(2 ** attempt)
        else:
            raise RuntimeError("Alpaca daily request exhausted retries")
        payload = response.json()
        for symbol, bars in (payload.get("bars") or {}).items():
            for bar in bars:
                rows.append(
                    {
                        "symbol": symbol,
                        "timestamp": bar["t"],
                        "date": str(bar["t"])[:10],
                        "open": bar["o"],
                        "high": bar["h"],
                        "low": bar["l"],
                        "close": bar["c"],
                        "volume": bar["v"],
                        "trade_count": bar.get("n"),
                        "vwap": bar.get("vw"),
                        "feed": "sip",
                        "adjustment": "split",
                    }
                )
        token = payload.get("next_page_token")
        if not token:
            return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--event-registry", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", default="2024-05-01")
    parser.add_argument("--end-exclusive", default="2026-05-01")
    parser.add_argument("--batch-size", type=int, default=40)
    args = parser.parse_args()
    if pd.Timestamp(args.end_exclusive) > HOLDOUT_START:
        raise RuntimeError("Daily request could access sealed holdout")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    registry = pd.read_parquet(args.event_registry)
    symbols = sorted(registry["symbol"].unique())
    env = load_env(args.env_file)
    session = requests.Session()
    session.headers.update(
        {
            "APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"],
            "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"],
        }
    )
    base_url = env.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets")
    rows: list[dict] = []
    for offset in range(0, len(symbols), args.batch_size):
        rows.extend(
            pull(
                session,
                base_url,
                symbols[offset : offset + args.batch_size],
                args.start,
                args.end_exclusive,
            )
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("Alpaca returned no targeted daily bars")
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[
        frame["date"].ge(pd.Timestamp(args.start))
        & frame["date"].le(CUTOFF)
        & frame["symbol"].isin(symbols)
    ].copy()
    if frame.duplicated(["symbol", "date"]).any():
        raise RuntimeError("Duplicate targeted daily symbol-date")
    if frame["date"].max() > CUTOFF:
        raise RuntimeError("Targeted daily artifact crosses holdout boundary")
    bad = (
        frame[["open", "high", "low", "close"]].isna().any(axis=1)
        | frame[["open", "high", "low", "close"]].le(0).any(axis=1)
        | frame["high"].lt(frame[["open", "close"]].max(axis=1))
        | frame["low"].gt(frame[["open", "close"]].min(axis=1))
    )
    if bad.any():
        raise RuntimeError(f"Invalid targeted daily OHLC rows: {int(bad.sum())}")
    output = args.output_dir / "alpaca_daily_split.parquet"
    frame.sort_values(["symbol", "date"]).to_parquet(output, index=False)
    report = {
        "status": "passed",
        "scope": "CAM-0007 event-registry symbols only",
        "source": "Alpaca historical SIP daily bars API",
        "adjustment": "split",
        "start": args.start,
        "end_exclusive": args.end_exclusive,
        "symbols_requested": len(symbols),
        "symbols_returned": int(frame["symbol"].nunique()),
        "rows": int(len(frame)),
        "min_loaded_date": str(frame["date"].min().date()),
        "max_loaded_date": str(frame["date"].max().date()),
        "holdout_rows_loaded": int((frame["date"] >= HOLDOUT_START).sum()),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    (args.output_dir / "alpaca_daily_split_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
