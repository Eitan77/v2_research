from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import time

import pandas as pd
import requests


BASE_URL = "https://data.alpaca.markets"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def candidate_events(parent_path: Path) -> pd.DataFrame:
    parent = pd.read_parquet(parent_path)
    names = {
        "q50": "q50_edge25_always_next_open_both_reversal_c5",
        "q60": "q60_edge25_always_next_open_both_reversal_c5",
    }
    frames = []
    for label, name in names.items():
        frame = parent[parent["variant"].eq(name)][
            ["date", "next_session", "symbol", "signal_return"]
        ].copy()
        frame[f"is_{label}"] = True
        frames.append(frame)
    events = frames[0].merge(
        frames[1],
        on=["date", "next_session", "symbol", "signal_return"],
        how="outer",
        suffixes=("_q50", "_q60"),
    )
    events["is_q50"] = events.get("is_q50", events.get("is_q50_q50")).fillna(
        False
    )
    events["is_q60"] = events.get("is_q60", events.get("is_q60_q60")).fillna(
        False
    )
    events = events[
        ["date", "next_session", "symbol", "signal_return", "is_q50", "is_q60"]
    ].copy()
    events["date"] = pd.to_datetime(events["date"])
    events["next_session"] = pd.to_datetime(events["next_session"])
    events["event_id"] = (
        events["date"].dt.strftime("%Y%m%d") + "_" + events["symbol"]
    )
    return events.sort_values(["date", "symbol"]).reset_index(drop=True)


def task_windows(events: pd.DataFrame) -> list[dict]:
    tasks = []
    for item in events.itertuples():
        for phase, session, clock in [
            ("entry", item.date, "15:59:00"),
            ("exit", item.next_session, "09:30:00"),
        ]:
            target = pd.Timestamp(
                f"{pd.Timestamp(session).date()} {clock}",
                tz="America/New_York",
            )
            tasks.append(
                {
                    "event_id": item.event_id,
                    "symbol": item.symbol,
                    "phase": phase,
                    "target_ts": target.tz_convert("UTC"),
                    "start": target.tz_convert("UTC") - pd.Timedelta(seconds=5),
                    "end": target.tz_convert("UTC") + pd.Timedelta(seconds=10),
                }
            )
    return tasks


def fetch_one(
    task: dict, kind: str, headers: dict[str, str]
) -> list[dict]:
    url = f"{BASE_URL}/v2/stocks/{task['symbol']}/{kind}"
    params = {
        "start": task["start"].isoformat(),
        "end": task["end"].isoformat(),
        "feed": "sip",
        "limit": 10000,
        "sort": "asc",
    }
    records = []
    for attempt in range(6):
        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code == 429 or response.status_code >= 500:
            time.sleep(min(2**attempt, 20))
            continue
        response.raise_for_status()
        payload = response.json()
        values = payload.get(kind, [])
        for value in values:
            value = dict(value)
            value.update(
                {
                    "event_id": task["event_id"],
                    "phase": task["phase"],
                    "target_ts": task["target_ts"].isoformat(),
                }
            )
            records.append(value)
        token = payload.get("next_page_token")
        if not token:
            return records
        params["page_token"] = token
    raise RuntimeError(
        f"request retries exhausted for {task['event_id']} {task['phase']} {kind}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-positions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    env = load_env(Path(".env.local"))
    headers = {
        "APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"],
    }
    events = candidate_events(args.parent_positions)
    if events["next_session"].max() > pd.Timestamp("2026-04-30"):
        raise RuntimeError("event requires sealed exit session")
    tasks = task_windows(events)
    outputs: dict[str, list[dict]] = {"quotes": [], "trades": []}
    futures = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        for task in tasks:
            for kind in ["quotes", "trades"]:
                future = executor.submit(fetch_one, task, kind, headers)
                futures[future] = (task, kind)
        for future in as_completed(futures):
            _, kind = futures[future]
            outputs[kind].extend(future.result())
    quotes = pd.DataFrame(outputs["quotes"])
    trades = pd.DataFrame(outputs["trades"])
    events.to_parquet(args.output_dir / "events.parquet", index=False)
    quotes.to_parquet(args.output_dir / "quotes.parquet", index=False)
    trades.to_parquet(args.output_dir / "trades.parquet", index=False)
    report = {
        "status": "passed",
        "event_count": int(len(events)),
        "window_count": int(len(tasks)),
        "quote_rows": int(len(quotes)),
        "trade_rows": int(len(trades)),
        "symbols": sorted(events["symbol"].unique().tolist()),
        "max_exit_session": str(events["next_session"].max().date()),
        "holdout_rows_requested": 0,
        "scope": "candidate event windows only",
    }
    (args.output_dir / "pull_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
