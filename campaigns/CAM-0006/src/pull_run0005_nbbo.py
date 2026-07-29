from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import time

import pandas as pd
import requests


BASE_URL = "https://data.alpaca.markets"
CUTOFF = pd.Timestamp("2026-04-30")


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def candidate_events(parent_path: Path, signals_path: Path) -> pd.DataFrame:
    parent = pd.read_parquet(parent_path)
    parent["date"] = pd.to_datetime(parent["date"])
    names = {
        "all_state": "tail10_anomaly1_allocation_top1_reclaim_all_all_c10",
        "vol_high": "tail10_anomaly1_market_state_top1_reclaim_all_vol_high_c10",
    }
    frames = []
    for label, name in names.items():
        frame = parent[parent["variant"].eq(name)][["date", "symbol"]].copy()
        frame[f"is_{label}"] = True
        frames.append(frame)
    events = frames[0].merge(
        frames[1], on=["date", "symbol"], how="outer", validate="one_to_one"
    )
    for label in names:
        events[f"is_{label}"] = events[f"is_{label}"].fillna(False)
    signals = pd.read_parquet(
        signals_path,
        columns=[
            "date",
            "symbol",
            "final_exit_minute",
            "entry_open",
            "exit_final",
        ],
    )
    signals["date"] = pd.to_datetime(signals["date"])
    events = events.merge(
        signals,
        on=["date", "symbol"],
        how="left",
        validate="one_to_one",
    )
    if events[["final_exit_minute", "entry_open", "exit_final"]].isna().any().any():
        raise RuntimeError("Missing event execution marks")
    events["event_id"] = (
        events["date"].dt.strftime("%Y%m%d") + "_" + events["symbol"]
    )
    return events.sort_values(["date", "symbol"]).reset_index(drop=True)


def task_windows(events: pd.DataFrame) -> list[dict]:
    tasks = []
    for item in events.itertuples():
        for phase, clock in (
            ("entry", "09:31:00"),
            ("exit", f"{item.final_exit_minute}:00"),
        ):
            target = pd.Timestamp(
                f"{pd.Timestamp(item.date).date()} {clock}",
                tz="America/New_York",
            )
            target_utc = target.tz_convert("UTC")
            tasks.append(
                {
                    "event_id": item.event_id,
                    "symbol": item.symbol,
                    "phase": phase,
                    "target_ts": target_utc,
                    "start": target_utc - pd.Timedelta(seconds=5),
                    "end": target_utc + pd.Timedelta(seconds=10),
                }
            )
    return tasks


def fetch_one(task: dict, kind: str, headers: dict[str, str]) -> list[dict]:
    url = f"{BASE_URL}/v2/stocks/{task['symbol']}/{kind}"
    params = {
        "start": task["start"].isoformat(),
        "end": task["end"].isoformat(),
        "feed": "sip",
        "limit": 10000,
        "sort": "asc",
    }
    records = []
    for attempt in range(7):
        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code == 429 or response.status_code >= 500:
            time.sleep(min(2**attempt, 20))
            continue
        response.raise_for_status()
        payload = response.json()
        for value in (payload.get(kind) or []):
            row = dict(value)
            row.update(
                {
                    "event_id": task["event_id"],
                    "phase": task["phase"],
                    "target_ts": task["target_ts"].isoformat(),
                }
            )
            records.append(row)
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
    parser.add_argument("--signals-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    env = load_env(Path(".env.local"))
    headers = {
        "APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"],
    }
    events = candidate_events(args.parent_positions, args.signals_path)
    if events["date"].max() > CUTOFF:
        raise RuntimeError("Event requires sealed session")
    tasks = task_windows(events)
    if any(task["target_ts"].tz_convert("America/New_York").date() > CUTOFF.date() for task in tasks):
        raise RuntimeError("Targeted request crosses sealed boundary")
    outputs: dict[str, list[dict]] = {"quotes": [], "trades": []}
    futures = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        for task in tasks:
            for kind in ("quotes", "trades"):
                future = executor.submit(fetch_one, task, kind, headers)
                futures[future] = kind
        for future in as_completed(futures):
            outputs[futures[future]].extend(future.result())
    quotes = pd.DataFrame(outputs["quotes"])
    trades = pd.DataFrame(outputs["trades"])
    events.to_parquet(args.output_dir / "events.parquet", index=False)
    quotes.to_parquet(args.output_dir / "quotes.parquet", index=False)
    trades.to_parquet(args.output_dir / "trades.parquet", index=False)
    report = {
        "status": "passed",
        "event_count": int(len(events)),
        "all_state_events": int(events["is_all_state"].sum()),
        "vol_high_events": int(events["is_vol_high"].sum()),
        "window_count": int(len(tasks)),
        "quote_rows": int(len(quotes)),
        "trade_rows": int(len(trades)),
        "symbols": sorted(events["symbol"].unique().tolist()),
        "max_session": str(events["date"].max().date()),
        "holdout_rows_requested": 0,
        "scope": "frozen candidate event windows only",
    }
    (args.output_dir / "pull_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
