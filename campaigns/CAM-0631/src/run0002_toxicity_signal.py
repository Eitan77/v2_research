from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from scipy.stats import rankdata


ROOT = Path(__file__).resolve().parents[3]
CAM = ROOT / "campaigns" / "CAM-0631"
OUT = CAM / "artifacts" / "RUN-0002"
CACHE = ROOT / "tmp" / "epdc_cam0631_run0002_not_committed"
ENV = ROOT / ".env.local"
SYMBOLS = ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "AMD", "AVGO", "MU", "TSLA", "JPM", "BAC", "WFC", "XOM", "CVX", "LLY", "UNH", "JNJ", "WMT", "COST", "HD", "DIS", "UBER", "PLTR"]
SESSIONS = ["2024-11-06", "2025-02-05", "2025-05-07", "2025-08-06", "2025-11-05", "2026-04-01"]
TRAIN_SESSIONS = set(SESSIONS[:4])
VALIDATION_SESSIONS = set(SESSIONS[4:])
WINDOWS = [("morning", "10:00:00", "10:02:00"), ("midday", "13:00:00", "13:02:00")]
HORIZONS = [0.25, 1, 5, 15, 30, 60]
FEATURES = ["spread_bps", "size_imbalance", "microprice_edge_bps", "quote_rate_1s", "mid_return_1s", "mid_return_5s", "trade_imbalance_1s"]
CUTOFF = pd.Timestamp("2026-04-30 23:59:59.999999999", tz="America/New_York")


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.interval = 60.0 / per_minute
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            scheduled = max(now, self.next_at)
            self.next_at = scheduled + self.interval
        delay = scheduled - now
        if delay > 0:
            time.sleep(delay)


THREAD_LOCAL = threading.local()
LIMITER = RateLimiter(180)


def get_session(key: str, secret: str) -> requests.Session:
    session = getattr(THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"})
        THREAD_LOCAL.session = session
    return session


def iso_z(value: pd.Timestamp) -> str:
    return value.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def job_times(date: str, start: str, end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    a = pd.Timestamp(f"{date} {start}", tz="America/New_York")
    # Request enough future state for the 60-second markout.
    b = pd.Timestamp(f"{date} {end}", tz="America/New_York") + pd.Timedelta(seconds=61)
    if b > CUTOFF:
        raise RuntimeError("request crosses discovery cutoff")
    return a, b


def fetch_endpoint(symbol: str, date: str, label: str, start: str, end: str, kind: str, key: str, secret: str) -> Path:
    target = CACHE / kind / f"{date}_{label}_{symbol}.parquet"
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    begin, finish = job_times(date, start, end)
    params = {"symbols": symbol, "start": iso_z(begin), "end": iso_z(finish), "feed": "sip", "limit": 10_000, "sort": "asc"}
    rows: list[dict] = []
    seen: set[str] = set()
    pages = 0
    while True:
        LIMITER.wait()
        session = get_session(key, secret)
        for attempt in range(8):
            response = session.get(f"https://data.alpaca.markets/v2/stocks/{kind}", params=params, timeout=60)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 7:
                    response.raise_for_status()
                time.sleep(min(30.0, 0.5 * 2**attempt))
                continue
            response.raise_for_status()
            payload = response.json()
            break
        values = payload.get(kind, {}).get(symbol, [])
        if not isinstance(values, list):
            raise RuntimeError(f"unexpected {kind} payload for {symbol}")
        rows.extend(values)
        pages += 1
        token = payload.get("next_page_token")
        if not token:
            break
        if token in seen:
            raise RuntimeError(f"repeated {kind} pagination token")
        seen.add(token)
        params["page_token"] = token
    if kind == "quotes":
        frame = pd.DataFrame({
            "ts": pd.to_datetime([row.get("t") for row in rows], utc=True, errors="coerce", format="mixed"),
            "bid": pd.to_numeric([row.get("bp") for row in rows], errors="coerce"),
            "ask": pd.to_numeric([row.get("ap") for row in rows], errors="coerce"),
            "bid_size": pd.to_numeric([row.get("bs") for row in rows], errors="coerce"),
            "ask_size": pd.to_numeric([row.get("as") for row in rows], errors="coerce"),
        })
        frame = frame.dropna().query("bid > 0 and ask >= bid and bid_size >= 0 and ask_size >= 0").sort_values("ts")
    else:
        frame = pd.DataFrame({
            "ts": pd.to_datetime([row.get("t") for row in rows], utc=True, errors="coerce", format="mixed"),
            "price": pd.to_numeric([row.get("p") for row in rows], errors="coerce"),
            "size": pd.to_numeric([row.get("s") for row in rows], errors="coerce"),
        })
        frame = frame.dropna().query("price > 0 and size > 0").sort_values("ts")
    if frame.empty:
        raise RuntimeError(f"empty {kind} frame {symbol} {date} {label}")
    frame["pages"] = pages
    frame.to_parquet(target, index=False)
    return target


def prior_values(times: np.ndarray, values: np.ndarray, targets: np.ndarray) -> np.ndarray:
    positions = np.searchsorted(times, targets, side="right") - 1
    out = np.full(len(targets), np.nan)
    valid = positions >= 0
    out[valid] = values[positions[valid]]
    return out


def future_values(times: np.ndarray, values: np.ndarray, targets: np.ndarray) -> np.ndarray:
    positions = np.searchsorted(times, targets, side="left")
    out = np.full(len(targets), np.nan)
    valid = positions < len(times)
    out[valid] = values[positions[valid]]
    return out


def build_events(symbol: str, date: str, label: str, start: str, end: str) -> pd.DataFrame:
    quotes = pd.read_parquet(CACHE / "quotes" / f"{date}_{label}_{symbol}.parquet").drop(columns="pages")
    trades = pd.read_parquet(CACHE / "trades" / f"{date}_{label}_{symbol}.parquet").drop(columns="pages")
    quotes.ts = pd.to_datetime(quotes.ts, utc=True)
    trades.ts = pd.to_datetime(trades.ts, utc=True)
    event_start = pd.Timestamp(f"{date} {start}", tz="America/New_York").tz_convert("UTC")
    event_end = pd.Timestamp(f"{date} {end}", tz="America/New_York").tz_convert("UTC")
    quotes = quotes.drop_duplicates("ts", keep="last").reset_index(drop=True)
    q_ns = quotes.ts.astype("int64").to_numpy()
    mid = ((quotes.bid + quotes.ask) / 2).to_numpy(float)
    spread = (quotes.ask - quotes.bid).to_numpy(float)
    denom = (quotes.bid_size + quotes.ask_size).to_numpy(float)
    imbalance = np.divide((quotes.bid_size - quotes.ask_size).to_numpy(float), denom, out=np.zeros(len(quotes)), where=denom > 0)
    micro = np.divide((quotes.ask * quotes.bid_size + quotes.bid * quotes.ask_size).to_numpy(float), denom, out=mid.copy(), where=denom > 0)
    micro_edge = micro - mid
    side = np.sign(micro_edge)
    side[side == 0] = np.sign(imbalance[side == 0])
    q_rate = np.arange(len(q_ns)) - np.searchsorted(q_ns, q_ns - int(1e9), side="left")
    prior1 = prior_values(q_ns, mid, q_ns - int(1e9))
    prior5 = prior_values(q_ns, mid, q_ns - int(5e9))
    ret1 = mid / prior1 - 1.0
    ret5 = mid / prior5 - 1.0
    t_ns = trades.ts.astype("int64").to_numpy()
    trade_mid = prior_values(q_ns, mid, t_ns)
    trade_sign = np.sign(trades.price.to_numpy(float) - trade_mid)
    signed_size = trade_sign * trades["size"].to_numpy(float)
    positive = np.where(signed_size > 0, signed_size, 0.0)
    negative = np.where(signed_size < 0, -signed_size, 0.0)
    cum_pos = np.r_[0.0, np.cumsum(positive)]
    cum_neg = np.r_[0.0, np.cumsum(negative)]
    right = np.searchsorted(t_ns, q_ns, side="right")
    left = np.searchsorted(t_ns, q_ns - int(1e9), side="left")
    buy = cum_pos[right] - cum_pos[left]
    sell = cum_neg[right] - cum_neg[left]
    trade_imb = np.divide(buy - sell, buy + sell, out=np.zeros(len(q_ns)), where=(buy + sell) > 0)
    data = {
        "symbol": symbol,
        "session": date,
        "window": label,
        "ts": quotes.ts,
        "side": side,
        "mid": mid,
        "spread_bps": spread / mid * 10_000,
        "size_imbalance": imbalance,
        "microprice_edge_bps": micro_edge / mid * 10_000,
        "quote_rate_1s": q_rate,
        "mid_return_1s": ret1,
        "mid_return_5s": ret5,
        "trade_imbalance_1s": trade_imb,
    }
    events = pd.DataFrame(data)
    for horizon in HORIZONS:
        future = future_values(q_ns, mid, q_ns + int(horizon * 1e9))
        events[f"markout_{horizon:g}s_bps"] = side * (future - mid) / mid * 10_000
    in_window = (events.ts >= event_start) & (events.ts < event_end)
    events = events[in_window & (events.side != 0)].replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURES + [f"markout_{h:g}s_bps" for h in HORIZONS])
    if events.empty:
        return events
    spread_cut = events.spread_bps.quantile(0.75)
    events = events[events.spread_bps >= spread_cut].copy()
    events["spread_cut_bps"] = spread_cut
    events["side"] = events.side.astype(int)
    return events


def fit_logit_irls(x: np.ndarray, y: np.ndarray, ridge: float = 1.0, iterations: int = 40) -> np.ndarray:
    beta = np.zeros(x.shape[1])
    penalty = np.eye(x.shape[1]) * ridge
    penalty[0, 0] = 0.0
    for _ in range(iterations):
        eta = np.clip(x @ beta, -30, 30)
        p = 1.0 / (1.0 + np.exp(-eta))
        w = np.maximum(p * (1 - p), 1e-6)
        z = eta + (y - p) / w
        hessian = x.T @ (w[:, None] * x) + penalty
        rhs = x.T @ (w * z)
        updated = np.linalg.solve(hessian, rhs)
        if np.max(np.abs(updated - beta)) < 1e-7:
            beta = updated
            break
        beta = updated
    return beta


def auc_score(y: np.ndarray, scores: np.ndarray) -> float:
    positives = y == 1
    n_pos = int(positives.sum())
    n_neg = len(y) - n_pos
    if not n_pos or not n_neg:
        return float("nan")
    ranks = rankdata(scores)
    return float((ranks[positives].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def main() -> None:
    load_dotenv(ENV)
    key = os.getenv("ALPACA_API_KEY_ID", "")
    secret = os.getenv("ALPACA_API_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError("credentials missing")
    jobs = [(symbol, date, label, start, end, kind) for symbol in SYMBOLS for date in SESSIONS for label, start, end in WINDOWS for kind in ("quotes", "trades")]
    with ThreadPoolExecutor(max_workers=12, thread_name_prefix="epdc") as executor:
        futures = {executor.submit(fetch_endpoint, *job, key, secret): job for job in jobs}
        for number, future in enumerate(as_completed(futures), 1):
            future.result()
            if number % 48 == 0:
                print(f"downloads {number}/{len(jobs)}", flush=True)
    frames = []
    job_stats = []
    for symbol in SYMBOLS:
        for date in SESSIONS:
            for label, start, end in WINDOWS:
                events = build_events(symbol, date, label, start, end)
                job_stats.append({"symbol": symbol, "session": date, "window": label, "candidate_events": len(events)})
                if len(events):
                    frames.append(events)
    if not frames:
        raise RuntimeError("no candidate events")
    data = pd.concat(frames, ignore_index=True)
    if pd.to_datetime(data.session).max() > pd.Timestamp("2026-04-30"):
        raise RuntimeError("holdout row loaded")
    train = data[data.session.isin(TRAIN_SESSIONS)].copy()
    validation = data[data.session.isin(VALIDATION_SESSIONS)].copy()
    target_col = "markout_5s_bps"
    y_all = (train[target_col].to_numpy() < 0).astype(float)
    # Deterministic thinning controls compute while preserving all validation events and all output summaries.
    max_train = 250_000
    train_model = train.iloc[np.linspace(0, len(train) - 1, min(max_train, len(train)), dtype=int)].copy()
    y = (train_model[target_col].to_numpy() < 0).astype(float)
    mean = train_model[FEATURES].mean().to_numpy()
    std = train_model[FEATURES].std().replace(0, 1).to_numpy()
    x = (train_model[FEATURES].to_numpy() - mean) / std
    x = np.c_[np.ones(len(x)), x]
    beta = fit_logit_irls(x, y)
    xv = (validation[FEATURES].to_numpy() - mean) / std
    xv = np.c_[np.ones(len(xv)), xv]
    probability = 1.0 / (1.0 + np.exp(-np.clip(xv @ beta, -30, 30)))
    validation["predicted_toxicity"] = probability
    validation["toxicity_target_5s"] = (validation[target_col] < 0).astype(int)
    low_cut, high_cut = np.quantile(probability, [0.2, 0.8])
    validation["bucket"] = np.where(probability <= low_cut, "low", np.where(probability >= high_cut, "high", "middle"))
    summary_rows = []
    group_cols = ["session", "bucket"]
    for keys, group in validation.groupby(group_cols):
        row = {"session": keys[0], "bucket": keys[1], "events": len(group), "mean_predicted_toxicity": group.predicted_toxicity.mean()}
        for horizon in HORIZONS:
            col = f"markout_{horizon:g}s_bps"
            row[f"mean_markout_{horizon:g}s_bps"] = group[col].mean()
            row[f"median_markout_{horizon:g}s_bps"] = group[col].median()
        summary_rows.append(row)
    bucket_summary = pd.DataFrame(summary_rows)
    symbol_summary = validation.groupby(["symbol", "bucket"])[[f"markout_{h:g}s_bps" for h in HORIZONS]].agg(["count", "mean"]).reset_index()
    # One row per 100ms cluster for auditable effective-event diagnostics and bounded Git artifact size.
    validation["cluster_100ms"] = validation.ts.dt.floor("100ms")
    cluster_cols = FEATURES + [f"markout_{h:g}s_bps" for h in HORIZONS] + ["predicted_toxicity", "toxicity_target_5s"]
    clustered = validation.groupby(["symbol", "session", "window", "cluster_100ms", "side", "bucket"], as_index=False)[cluster_cols].mean()
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(job_stats).to_csv(OUT / "job_attrition.csv", index=False)
    bucket_summary.to_csv(OUT / "validation_bucket_markouts.csv", index=False)
    symbol_summary.to_csv(OUT / "validation_symbol_markouts.csv", index=False)
    clustered.to_parquet(OUT / "validation_events_100ms.parquet", index=False)
    coefficients = pd.DataFrame({"feature": ["intercept"] + FEATURES, "coefficient": beta})
    coefficients.to_csv(OUT / "model_coefficients.csv", index=False)
    full_low = validation[validation.bucket == "low"]
    full_high = validation[validation.bucket == "high"]
    report = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "planned_jobs": len(jobs),
        "executed_jobs": len(jobs),
        "symbols": len(SYMBOLS),
        "sessions": SESSIONS,
        "train_candidate_events": len(train),
        "train_model_events": len(train_model),
        "validation_candidate_events": len(validation),
        "validation_effective_100ms_clusters": len(clustered),
        "minimum_loaded_session": min(SESSIONS),
        "maximum_loaded_session": max(SESSIONS),
        "holdout_rows_loaded": 0,
        "model_target": "negative_side_signed_5s_midpoint_markout",
        "validation_auc": auc_score(validation.toxicity_target_5s.to_numpy(), probability),
        "low_bucket_share": float((validation.bucket == "low").mean()),
        "high_bucket_share": float((validation.bucket == "high").mean()),
        "low_bucket_mean_markouts_bps": {str(h): float(full_low[f"markout_{h:g}s_bps"].mean()) for h in HORIZONS},
        "high_bucket_mean_markouts_bps": {str(h): float(full_high[f"markout_{h:g}s_bps"].mean()) for h in HORIZONS},
        "raw_cache_committed": False,
        "fill_simulation_performed": False,
        "decision_gate": "continue_only_if_low_toxicity_markouts_are_positive_and_stable_across_validation_sessions_symbols_and_horizons",
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
