from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd
import requests
import exchange_calendars as xcals

from cam0004 import CUTOFF, HOLDOUT_START, load_membership, validate_cutoff


def load_local_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    required = ["ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"]
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise RuntimeError(f"missing local credential variables: {missing}")
    return values


def batches(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def pull_batch(
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
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", default="2024-05-01")
    parser.add_argument("--end-exclusive", default="2026-05-01")
    parser.add_argument("--batch-size", type=int, default=40)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if pd.Timestamp(args.end_exclusive) > HOLDOUT_START:
        raise RuntimeError("end-exclusive would permit sealed holdout access")
    env = load_local_env(args.env_file)
    base_url = env.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets")
    membership = load_membership(start=args.start, end=CUTOFF.date().isoformat())
    symbols = sorted(set(membership["symbol"].unique()) | {"QQQ"})

    session = requests.Session()
    session.headers.update(
        {
            "APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"],
            "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"],
        }
    )
    rows: list[dict] = []
    for batch in batches(symbols, args.batch_size):
        rows.extend(
            pull_batch(
                session, base_url, batch, args.start, args.end_exclusive
            )
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("Alpaca returned no split-adjusted daily bars")
    frame["date"] = pd.to_datetime(frame["date"])
    validate_cutoff(frame)
    duplicates = int(frame.duplicated(["symbol", "date"]).sum())
    if duplicates:
        raise RuntimeError(f"duplicate symbol-date rows: {duplicates}")
    invalid = int(
        (
            frame[["open", "high", "low", "close"]].isna().any(axis=1)
            | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
            | (frame["high"] < frame[["open", "close"]].max(axis=1))
            | (frame["low"] > frame[["open", "close"]].min(axis=1))
        ).sum()
    )
    if invalid:
        raise RuntimeError(f"invalid daily OHLC rows: {invalid}")
    calendar = xcals.get_calendar("XNYS")
    sessions = pd.DatetimeIndex(
        calendar.sessions_in_range(args.start, CUTOFF.date().isoformat())
    ).tz_localize(None)
    session_membership = membership[membership["date"].isin(sessions)]
    coverage = session_membership[["date", "symbol"]].merge(
        frame[["date", "symbol"]],
        on=["date", "symbol"],
        how="left",
        indicator=True,
        validate="one_to_one",
    )
    report = {
        "status": "passed",
        "source": "Alpaca historical SIP daily bars",
        "adjustment": "split",
        "start": args.start,
        "end_exclusive": args.end_exclusive,
        "max_loaded_date": str(frame["date"].max().date()),
        "holdout_rows_loaded": int((frame["date"] >= HOLDOUT_START).sum()),
        "symbols_requested": len(symbols),
        "symbols_returned": int(frame["symbol"].nunique()),
        "rows": int(len(frame)),
        "calendar_membership_rows_excluded": int(
            len(membership) - len(session_membership)
        ),
        "member_trading_symbol_dates": int(len(coverage)),
        "covered_member_symbol_dates": int((coverage["_merge"] == "both").sum()),
        "missing_member_symbol_dates": int((coverage["_merge"] != "both").sum()),
        "invalid_ohlc_rows": invalid,
    }
    frame.sort_values(["date", "symbol"]).to_parquet(
        args.output_dir / "alpaca_daily_split.parquet", index=False
    )
    (args.output_dir / "alpaca_daily_split_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
