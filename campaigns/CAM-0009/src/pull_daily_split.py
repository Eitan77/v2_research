from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import pandas as pd
import requests


SYMBOLS = (
    "ADI",
    "AMD",
    "AMAT",
    "ARM",
    "ASML",
    "AVGO",
    "INTC",
    "KLAC",
    "LRCX",
    "MCHP",
    "MPWR",
    "MRVL",
    "MU",
    "NVDA",
    "NXPI",
    "ON",
    "QCOM",
    "TXN",
    "QQQ",
    "SMH",
    "SOXX",
)
CUTOFF = pd.Timestamp("2026-04-30")
HOLDOUT_START = pd.Timestamp("2026-05-01")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
            raise RuntimeError(f"Missing credential variable {key}")
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", default="2024-05-01")
    parser.add_argument("--end-exclusive", default="2026-05-01")
    args = parser.parse_args()
    if pd.Timestamp(args.end_exclusive) > HOLDOUT_START:
        raise RuntimeError("Daily request could cross sealed holdout")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    env = load_env(args.env_file)
    base_url = env.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets")
    session = requests.Session()
    session.headers.update(
        {
            "APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"],
            "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"],
        }
    )
    rows: list[dict] = []
    token = None
    pages = 0
    while True:
        params = {
            "symbols": ",".join(SYMBOLS),
            "timeframe": "1Day",
            "start": f"{args.start}T00:00:00Z",
            "end": f"{args.end_exclusive}T00:00:00Z",
            "adjustment": "split",
            "feed": "sip",
            "sort": "asc",
            "limit": 10000,
        }
        if token:
            params["page_token"] = token
        response = session.get(
            f"{base_url.rstrip('/')}/v2/stocks/bars",
            params=params,
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
            break
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("Alpaca returned no targeted daily bars")
    frame["date"] = pd.to_datetime(frame["date"])
    if frame["date"].max() > CUTOFF:
        raise RuntimeError("Targeted daily artifact crosses holdout boundary")
    if frame.duplicated(["symbol", "date"]).any():
        raise RuntimeError("Duplicate targeted daily symbol-date")
    if set(frame["symbol"]) != set(SYMBOLS):
        raise RuntimeError(
            f"Targeted daily symbol mismatch: {sorted(frame['symbol'].unique())}"
        )
    bad = (
        frame[["open", "high", "low", "close"]].isna().any(axis=1)
        | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | frame["high"].lt(frame[["open", "close"]].max(axis=1))
        | frame["low"].gt(frame[["open", "close"]].min(axis=1))
    )
    if bad.any():
        raise RuntimeError(f"Invalid targeted daily rows: {int(bad.sum())}")
    output = args.output_dir / "alpaca_daily_split.parquet"
    frame.sort_values(["symbol", "date"]).to_parquet(output, index=False)
    report = {
        "status": "passed",
        "scope": "CAM-0009 declared 18 semiconductor symbols plus QQQ/SMH/SOXX",
        "source": "Alpaca historical SIP daily bars API",
        "adjustment": "split",
        "start": args.start,
        "end_exclusive": args.end_exclusive,
        "symbols_requested": len(SYMBOLS),
        "symbols_returned": int(frame["symbol"].nunique()),
        "rows": int(len(frame)),
        "pages": pages,
        "minimum_date": str(frame["date"].min().date()),
        "maximum_loaded_date": str(frame["date"].max().date()),
        "holdout_rows_loaded": int(
            frame["date"].ge(HOLDOUT_START).sum()
        ),
        "data_sha256": sha256(output),
        "credentials_recorded": False,
    }
    (args.output_dir / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
