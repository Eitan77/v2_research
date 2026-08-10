from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests


WORKSPACE = Path(__file__).resolve().parents[3]
SHARED = WORKSPACE / "campaigns" / "CAM-0600" / "artifacts" / "shared"
ROLES_PATH = SHARED / "quote_roles.parquet"
OUTPUT = SHARED / "remote_quote_role_matches.parquet"
WORKERS = 16
BATCH_SIZE = 100


def load_env(path: Path) -> dict[str, str]:
    values = {}
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


def batches(values: list[str], size: int) -> list[list[str]]:
    return [values[i:i+size] for i in range(0, len(values), size)]


def request_task(task: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    target = pd.Timestamp(task["target_ts"])
    after = task["direction"] == "after"
    window_seconds = int(task["window_seconds"])
    start = target if after else target - pd.Timedelta(seconds=window_seconds)
    end = target + pd.Timedelta(seconds=window_seconds) if after else target
    params = {
        "symbols": ",".join(task["symbols"]),
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "limit": 10000,
        "feed": "sip",
        "sort": "asc",
    }
    headers = {
        "APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"],
    }
    url = env.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/") + "/v2/stocks/quotes"
    quotes: dict[str, list[dict]] = {symbol: [] for symbol in task["symbols"]}
    pages = 0
    token = None
    attempts = 0
    while True:
        request_params = dict(params)
        if token:
            request_params["page_token"] = token
        for retry in range(8):
            attempts += 1
            response = requests.get(url, headers=headers, params=request_params, timeout=60)
            if response.status_code == 429:
                time.sleep(min(15.0, 1.0 + retry*2.0))
                continue
            response.raise_for_status()
            break
        else:
            raise RuntimeError("Alpaca quote role request exhausted retries")
        payload = response.json()
        pages += 1
        for symbol, values in (payload.get("quotes") or {}).items():
            quotes.setdefault(symbol, []).extend(values)
        token = payload.get("next_page_token")
        if not token:
            break
    rows = []
    for symbol in task["symbols"]:
        values = quotes.get(symbol) or []
        valid = [q for q in values if q.get("bp") and q.get("ap") and q["bp"] > 0 and q["ap"] >= q["bp"]]
        if not valid:
            continue
        valid.sort(key=lambda q: q["t"])
        quote = valid[0] if after else valid[-1]
        for role in task["roles_by_symbol"][symbol]:
            rows.append({
                "symbol": symbol, "target_ts": target, "role": role,
                "quote_ts": pd.Timestamp(quote["t"]), "bid_price": quote["bp"], "ask_price": quote["ap"],
                "bid_size": quote.get("bs"), "ask_size": quote.get("as"),
                "feed": "sip", "provider": "alpaca_remote_role_pull",
            })
    return {"rows": rows, "pages": pages, "attempts": attempts, "requested": len(task["symbols"])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--roles", type=Path, default=ROLES_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--window-seconds", type=int, default=1)
    parser.add_argument("--workers", type=int, default=WORKERS)
    args = parser.parse_args()
    env = load_env(WORKSPACE / ".env.local")
    roles = pd.read_parquet(args.roles)
    roles["target_ts"] = pd.to_datetime(roles["target_ts"], utc=True)
    if (roles["target_ts"] >= pd.Timestamp("2026-05-01", tz="UTC")).any():
        raise RuntimeError("remote quote roles cross holdout")
    roles["direction"] = roles["role"].map(lambda x: "before" if x == "exit_bid_before" else "after")
    tasks = []
    for (target, direction), group in roles.groupby(["target_ts", "direction"], sort=True):
        roles_by_symbol = group.groupby("symbol")["role"].apply(list).to_dict()
        symbols = sorted(roles_by_symbol)
        for batch in batches(symbols, BATCH_SIZE):
            tasks.append({"target_ts": target, "direction": direction, "symbols": batch,
                          "roles_by_symbol": {s: roles_by_symbol[s] for s in batch},
                          "window_seconds": args.window_seconds})
    all_rows = []
    pages = attempts = requested = completed = 0
    failed_tasks = 0
    failure_messages = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(request_task, task, env) for task in tasks]
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                failed_tasks += 1
                failure_messages.append(str(exc))
                continue
            all_rows.extend(result["rows"])
            pages += result["pages"]
            attempts += result["attempts"]
            requested += result["requested"]
            completed += 1
            if completed % 100 == 0:
                print(f"completed_tasks={completed}/{len(tasks)} matched_rows={len(all_rows)}", flush=True)
    matched = pd.DataFrame(all_rows)
    if matched.empty:
        matched = pd.DataFrame(columns=["symbol", "target_ts", "role", "quote_ts", "bid_price", "ask_price",
                                                "bid_size", "ask_size", "feed", "provider"])
    matched = matched.drop_duplicates(["symbol", "target_ts", "role"], keep="first")
    matched.to_parquet(args.output, index=False)
    merged = roles.merge(matched[["symbol", "target_ts", "role"]], on=["symbol", "target_ts", "role"], how="left", indicator=True)
    report = {
        "status": "passed", "generated_utc": datetime.now(timezone.utc).isoformat(),
        "workers": args.workers, "batch_size": BATCH_SIZE, "tasks": len(tasks), "failed_tasks": failed_tasks,
        "failure_messages": sorted(set(failure_messages))[:10], "request_symbol_units": requested,
        "http_attempts": attempts, "pages": pages, "roles": int(len(roles)), "matched_roles": int(len(matched)),
        "missing_roles": int((merged["_merge"] != "both").sum()),
        "coverage_rate": float((merged["_merge"] == "both").mean()),
        "symbols_matched": int(matched["symbol"].nunique()) if len(matched) else 0,
        "holdout_rows_loaded": int((pd.to_datetime(matched["quote_ts"], utc=True) >= pd.Timestamp("2026-05-01", tz="UTC")).sum()) if len(matched) else 0,
        "request_windows_seconds": args.window_seconds,
        "credentials_recorded": False,
    }
    args.output.with_name(args.output.stem + "_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
