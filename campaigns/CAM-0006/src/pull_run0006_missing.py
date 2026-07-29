from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

import pandas as pd

from pull_run0005_nbbo import fetch_one, load_env


CUTOFF = pd.Timestamp("2026-04-30")


def target_for(event: pd.Series, phase: str) -> pd.Timestamp:
    clock = "09:31:00" if phase == "entry" else f"{event['final_exit_minute']}:00"
    return pd.Timestamp(
        f"{pd.Timestamp(event['date']).date()} {clock}",
        tz="America/New_York",
    ).tz_convert("UTC")


def has_valid_quote(
    quotes: pd.DataFrame, event_id: str, phase: str
) -> bool:
    frame = quotes[
        quotes["event_id"].eq(event_id) & quotes["phase"].eq(phase)
    ].copy()
    if frame.empty:
        return False
    frame["t"] = pd.to_datetime(frame["t"], utc=True)
    frame["target_ts"] = pd.to_datetime(frame["target_ts"], utc=True)
    valid = frame[
        frame["t"].ge(frame["target_ts"])
        & frame["ap"].gt(0)
        & frame["bp"].gt(0)
        & frame["ap"].ge(frame["bp"])
    ]
    return not valid.empty


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run0005-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    events = pd.read_parquet(args.run0005_dir / "raw" / "events.parquet")
    quotes = pd.read_parquet(args.run0005_dir / "raw" / "quotes.parquet")
    replay = pd.read_parquet(args.run0005_dir / "event_replay.parquet")
    events["date"] = pd.to_datetime(events["date"])
    if events["date"].max() > CUTOFF:
        raise RuntimeError("Sealed event loaded")
    incomplete_ids = set(replay.loc[~replay["quote_complete"], "event_id"])
    tasks = []
    for _, event in events[events["event_id"].isin(incomplete_ids)].iterrows():
        for phase in ("entry", "exit"):
            if has_valid_quote(quotes, event["event_id"], phase):
                continue
            target = target_for(event, phase)
            tasks.append(
                {
                    "event_id": event["event_id"],
                    "symbol": event["symbol"],
                    "phase": phase,
                    "target_ts": target,
                    "start": target,
                    "end": target + pd.Timedelta(seconds=120),
                }
            )
    if len(tasks) != 12:
        raise RuntimeError(f"Expected 12 missing phases, got {len(tasks)}")
    env = load_env(Path(".env.local"))
    headers = {
        "APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"],
    }
    outputs: dict[str, list[dict]] = {"quotes": [], "trades": []}
    futures = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        for task in tasks:
            for kind in ("quotes", "trades"):
                future = executor.submit(fetch_one, task, kind, headers)
                futures[future] = kind
        for future in as_completed(futures):
            outputs[futures[future]].extend(future.result())
    quote_extension = pd.DataFrame(outputs["quotes"])
    trade_extension = pd.DataFrame(outputs["trades"])
    pd.DataFrame(tasks).assign(
        target_ts=lambda x: x["target_ts"].astype(str),
        start=lambda x: x["start"].astype(str),
        end=lambda x: x["end"].astype(str),
    ).to_parquet(args.output_dir / "tasks.parquet", index=False)
    quote_extension.to_parquet(args.output_dir / "quotes.parquet", index=False)
    trade_extension.to_parquet(args.output_dir / "trades.parquet", index=False)
    report = {
        "status": "passed",
        "missing_event_count": int(len(incomplete_ids)),
        "requested_phase_count": int(len(tasks)),
        "quote_rows": int(len(quote_extension)),
        "trade_rows": int(len(trade_extension)),
        "max_session": str(events["date"].max().date()),
        "holdout_rows_requested": 0,
        "scope": "RUN-0005 missing event phases only",
    }
    (args.output_dir / "pull_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
