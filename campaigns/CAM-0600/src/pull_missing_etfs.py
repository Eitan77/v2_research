from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pandas as pd
import requests


WORKSPACE = Path(__file__).resolve().parents[3]
OUTPUT = WORKSPACE / "campaigns" / "CAM-0600" / "artifacts" / "shared" / "supplemental_etf_daily.parquet"
SYMBOLS = ("BIL", "SHY", "XLB", "XLC", "XLRE")
START = "2019-01-01T00:00:00Z"
END = "2026-05-01T00:00:00Z"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    for key in ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"):
        if not values.get(key):
            raise RuntimeError(f"missing {key}")
    return values


def main() -> None:
    env = load_env(WORKSPACE / ".env.local")
    session = requests.Session()
    session.headers.update({
        "APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"],
    })
    params = {
        "symbols": ",".join(SYMBOLS),
        "timeframe": "1Day",
        "start": START,
        "end": END,
        "limit": 10000,
        "adjustment": "all",
        "feed": "sip",
        "sort": "asc",
    }
    rows: list[dict] = []
    pages = 0
    token = None
    while True:
        request = dict(params)
        if token:
            request["page_token"] = token
        response = session.get(
            env.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/") + "/v2/stocks/bars",
            params=request,
            timeout=60,
        )
        if response.status_code == 429:
            time.sleep(2.0)
            continue
        response.raise_for_status()
        payload = response.json()
        pages += 1
        for symbol, bars in (payload.get("bars") or {}).items():
            for bar in bars:
                rows.append({
                    "symbol": symbol,
                    "date": str(bar["t"])[:10],
                    "open": bar["o"],
                    "high": bar["h"],
                    "low": bar["l"],
                    "close": bar["c"],
                    "volume": bar["v"],
                    "feed": "sip",
                    "adjustment": "all",
                })
        token = payload.get("next_page_token")
        if not token:
            break
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("Alpaca returned no supplemental ETF bars")
    frame["date"] = pd.to_datetime(frame["date"])
    if (frame["date"] >= pd.Timestamp("2026-05-01")).any():
        raise RuntimeError("supplemental ETF pull crossed holdout")
    if frame.duplicated(["date", "symbol"]).any():
        raise RuntimeError("duplicate supplemental ETF bars")
    missing = sorted(set(SYMBOLS) - set(frame["symbol"].unique()))
    if missing:
        raise RuntimeError(f"missing supplemental ETFs: {missing}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    frame.sort_values(["date", "symbol"]).to_parquet(OUTPUT, index=False)
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    report = {
        "status": "passed",
        "provider": "Alpaca Market Data API",
        "feed": "sip",
        "adjustment": "all",
        "symbols": list(SYMBOLS),
        "requested_start": START,
        "requested_end_exclusive": END,
        "rows": int(len(frame)),
        "pages": pages,
        "minimum_date": str(frame["date"].min().date()),
        "maximum_date": str(frame["date"].max().date()),
        "holdout_rows_loaded": 0,
        "rows_by_symbol": frame.groupby("symbol").size().astype(int).to_dict(),
        "output_sha256": digest,
        "credentials_recorded": False,
    }
    OUTPUT.with_name("supplemental_etf_daily_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
