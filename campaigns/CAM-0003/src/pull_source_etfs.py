from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import requests


SYMBOLS = ["EEM", "FXI", "EFA", "VWO", "IYR"]
START = "2024-08-01T00:00:00Z"
END = "2026-05-01T00:00:00Z"
URL = "https://data.alpaca.markets/v2/stocks/bars"


def load_local_env(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    repo = Path(__file__).resolve().parents[3]
    load_local_env(repo / ".env.local")
    key = os.environ.get("ALPACA_API_KEY_ID")
    secret = os.environ.get("ALPACA_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Alpaca credentials unavailable")

    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    params = {
        "symbols": ",".join(SYMBOLS),
        "timeframe": "1Min",
        "start": START,
        "end": END,
        "limit": 10000,
        "adjustment": "raw",
        "feed": "sip",
        "sort": "asc",
    }
    rows: list[dict] = []
    token = None
    pages = 0
    while True:
        request_params = dict(params)
        if token:
            request_params["page_token"] = token
        response = requests.get(URL, headers=headers, params=request_params, timeout=60)
        response.raise_for_status()
        payload = response.json()
        pages += 1
        for symbol, bars in payload.get("bars", {}).items():
            for bar in bars:
                rows.append(
                    {
                        "symbol": symbol,
                        "timestamp": bar["t"],
                        "open": bar["o"],
                        "high": bar["h"],
                        "low": bar["l"],
                        "close": bar["c"],
                        "volume": bar["v"],
                        "trade_count": bar.get("n"),
                        "vwap": bar.get("vw"),
                    }
                )
        token = payload.get("next_page_token")
        if not token:
            break

    d = pd.DataFrame(rows)
    if d.empty:
        raise RuntimeError("Alpaca returned no bars")
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True)
    d = d.drop_duplicates(["symbol", "timestamp"], keep="last").sort_values(["symbol", "timestamp"])
    if set(d["symbol"]) != set(SYMBOLS):
        raise RuntimeError(f"symbol coverage mismatch: {sorted(d['symbol'].unique())}")
    if d["timestamp"].max() >= pd.Timestamp("2026-05-01", tz="UTC"):
        raise RuntimeError("sealed holdout timestamp loaded")
    a.output.parent.mkdir(parents=True, exist_ok=True)
    d.to_parquet(a.output, index=False)
    manifest = {
        "provider": "Alpaca Market Data API",
        "feed": "sip",
        "adjustment": "raw",
        "timeframe": "1Min",
        "symbols": SYMBOLS,
        "requested_start": START,
        "requested_end_exclusive": END,
        "rows": int(len(d)),
        "pages": pages,
        "min_timestamp": d["timestamp"].min().isoformat(),
        "max_timestamp": d["timestamp"].max().isoformat(),
        "rows_on_or_after_holdout": int((d["timestamp"] >= pd.Timestamp("2026-05-01", tz="UTC")).sum()),
        "rows_by_symbol": {k: int(v) for k, v in d.groupby("symbol").size().items()},
        "data_sha256": sha256(a.output),
        "credentials_recorded": False,
    }
    manifest_path = a.output.with_name(a.output.stem + "_manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
