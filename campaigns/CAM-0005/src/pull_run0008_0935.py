from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from pull_run0006_nbbo import fetch_one, load_env


def task_windows(events: pd.DataFrame) -> list[dict]:
    tasks: list[dict] = []
    for item in events.itertuples():
        target = (
            pd.Timestamp(item.next_session)
            .tz_localize("America/New_York")
            + pd.Timedelta(hours=9, minutes=35)
        ).tz_convert("UTC")
        tasks.append(
            {
                "event_id": item.event_id,
                "symbol": item.symbol,
                "phase": "exit_0935",
                "target_ts": target,
                "start": target - pd.Timedelta(seconds=5),
                "end": target + pd.Timedelta(seconds=10),
            }
        )
    return tasks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    events = pd.read_parquet(args.events_path)
    events["date"] = pd.to_datetime(events["date"])
    events["next_session"] = pd.to_datetime(events["next_session"])
    if len(events) != 134 or events["event_id"].nunique() != 134:
        raise RuntimeError("Expected the frozen 134-event union")
    if events["next_session"].max() >= pd.Timestamp("2026-05-01"):
        raise RuntimeError("Holdout request blocked")
    tasks = task_windows(events)
    env = load_env(Path(".env.local"))
    headers = {
        "APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"],
    }
    results: dict[str, list[dict]] = {"quotes": [], "trades": []}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(fetch_one, task, kind, headers): (task, kind)
            for task in tasks
            for kind in ("quotes", "trades")
        }
        for future in as_completed(futures):
            task, kind = futures[future]
            results[kind].extend(future.result())

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for kind in ("quotes", "trades"):
        frame = pd.DataFrame(results[kind])
        frame.to_parquet(args.output_dir / f"{kind}.parquet", index=False)
    quote_count = len(results["quotes"])
    trade_count = len(results["trades"])
    report = {
        "status": "passed",
        "event_count": int(len(events)),
        "window_count": int(len(tasks)),
        "quote_rows": int(quote_count),
        "trade_rows": int(trade_count),
        "symbols": sorted(events["symbol"].unique().tolist()),
        "max_exit_session": str(events["next_session"].max().date()),
        "holdout_rows_requested": 0,
        "scope": "same frozen candidate events, 09:35 exits only",
    }
    (args.output_dir / "pull_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
