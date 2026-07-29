from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import time

import duckdb
import pandas as pd
import requests


BASE_URL = "https://data.alpaca.markets/v1beta1/news"
START = pd.Timestamp("2025-01-03 00:00:00", tz="America/New_York")
END = pd.Timestamp("2026-05-01 00:00:00", tz="America/New_York")


def load_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def qqq_union(catalog: Path) -> list[str]:
    con = duckdb.connect(str(catalog), read_only=True)
    try:
        symbols = con.execute(
            """
            SELECT DISTINCT symbol
            FROM qqq_pit_membership_daily
            WHERE is_member
              AND try_cast(date AS DATE)
                  BETWEEN DATE '2025-01-03' AND DATE '2026-04-30'
            ORDER BY symbol
            """
        ).fetch_df()["symbol"].tolist()
    finally:
        con.close()
    return symbols


def tasks(symbols: list[str]) -> list[dict]:
    months = pd.date_range(START.normalize(), END.normalize(), freq="MS")
    boundaries = sorted(set([START, *months.tolist(), END]))
    result = []
    for begin, finish in zip(boundaries[:-1], boundaries[1:], strict=True):
        if begin >= finish:
            continue
        for offset in range(0, len(symbols), 30):
            result.append(
                {
                    "symbols": symbols[offset : offset + 30],
                    "start": begin.tz_convert("UTC"),
                    "end": finish.tz_convert("UTC"),
                }
            )
    return result


def fetch(task: dict, headers: dict[str, str]) -> list[dict]:
    params = {
        "symbols": ",".join(task["symbols"]),
        "start": task["start"].isoformat(),
        "end": task["end"].isoformat(),
        "sort": "asc",
        "limit": 50,
        "include_content": "false",
    }
    rows = []
    while True:
        response = None
        for attempt in range(7):
            response = requests.get(
                BASE_URL, headers=headers, params=params, timeout=30
            )
            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(min(2**attempt, 20))
                continue
            response.raise_for_status()
            break
        else:
            status = response.status_code if response is not None else "none"
            raise RuntimeError(f"News page retries exhausted, status={status}")
        payload = response.json()
        rows.extend(payload.get("news") or [])
        token = payload.get("next_page_token")
        if not token:
            return rows
        params["page_token"] = token
        time.sleep(0.10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    symbols = qqq_union(args.catalog)
    request_tasks = tasks(symbols)
    if any(task["end"] > END.tz_convert("UTC") for task in request_tasks):
        raise RuntimeError("Request crosses sealed boundary")
    env = load_env(Path(".env.local"))
    headers = {
        "APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"],
    }
    rows = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(fetch, task, headers) for task in request_tasks]
        for future in as_completed(futures):
            rows.extend(future.result())
    frame = pd.DataFrame(rows)
    provider_out_of_window_rows = 0
    if not frame.empty:
        frame = frame.drop_duplicates(subset=["id"]).sort_values("created_at")
        created = pd.to_datetime(frame["created_at"], utc=True).dt.tz_convert(
            "America/New_York"
        )
        if created.max() >= END:
            raise RuntimeError("Downloaded news crosses sealed local date")
        in_window = created.ge(START) & created.lt(END)
        provider_out_of_window_rows = int((~in_window).sum())
        frame = frame[in_window].copy()
    frame.to_parquet(args.output_dir / "news.parquet", index=False)
    report = {
        "status": "passed",
        "symbol_union_count": len(symbols),
        "request_task_count": len(request_tasks),
        "news_rows": int(len(frame)),
        "provider_out_of_window_rows_rejected": provider_out_of_window_rows,
        "minimum_created_at": (
            str(frame["created_at"].min()) if len(frame) else None
        ),
        "maximum_created_at": (
            str(frame["created_at"].max()) if len(frame) else None
        ),
        "requested_local_start": str(START),
        "requested_local_end_exclusive": str(END),
        "holdout_rows_requested": 0,
        "scope": "point-in-time QQQ union missing earnings-event metadata interval",
    }
    (args.output_dir / "pull_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
