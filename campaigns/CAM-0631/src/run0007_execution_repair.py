from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from run0002_toxicity_signal import TRAIN_SESSIONS
from run0003_symmetric_cluster import clustered_data
from run0005_confirmation import CONFIRMATION, confirmation_clusters, load_package
from run0006_queue_sim import load_path, qindex_after


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0631" / "artifacts" / "RUN-0007"
DEV = CONFIRMATION[:6]
VALIDATION = CONFIRMATION[6:]
QUANTILES = [0.05, 0.10, 0.20]
SPREAD_CENTS = [1, 2, 3]
SIZES = [1.0, 10.0, 100.0]
LATENCIES = [250, 500]
TTLS = [1, 5, 15]
QUEUES = ["base", "conservative"]
EXITS = [5, 15]
SLIPPAGES = [1, 2]
COMMISSION = 0.004
SLOT_WEIGHT = 0.05
MAX_SLOTS = 20
STOP_BP = 15


def score(frame: pd.DataFrame, package: dict) -> np.ndarray:
    x = np.c_[np.ones(len(frame)), (frame[package["features"]].to_numpy() - np.asarray(package["mean"])) / np.asarray(package["std"])]
    return 1 / (1 + np.exp(-np.clip(x @ np.asarray(package["beta_intercept_then_features"]), -30, 30)))


def training_cuts(package: dict) -> dict[float, float]:
    train = clustered_data()
    train = train[train.session.isin(TRAIN_SESSIONS)]
    probability = score(train, package)
    return {quantile: float(np.quantile(probability, quantile)) for quantile in QUANTILES}


def enrich_candidates(package: dict) -> tuple[pd.DataFrame, dict]:
    candidates, _ = confirmation_clusters()
    candidates["predicted_toxicity"] = score(candidates, package)
    paths = {(symbol, session, window): load_path(symbol, session, window) for symbol, session, window in candidates[["symbol", "session", "window"]].drop_duplicates().itertuples(index=False, name=None)}
    spreads = []
    for row in candidates.itertuples():
        path = paths[(row.symbol, row.session, row.window)]
        signal_ns = pd.Timestamp(row.cluster_100ms).value
        index = int(np.searchsorted(path["q_ns"], signal_ns, side="right") - 1)
        spreads.append((path["ask"][index] - path["bid"][index]) * 100 if index >= 0 else np.nan)
    candidates["spread_cents"] = spreads
    return candidates, paths


def thin(frame: pd.DataFrame) -> pd.DataFrame:
    selected = []
    for _, group in frame.sort_values("cluster_100ms").groupby(["session", "symbol", "window"]):
        last = None
        for row in group.itertuples():
            if last is None or (row.cluster_100ms - last).total_seconds() >= 60:
                selected.append(row._asdict())
                last = row.cluster_100ms
    return pd.DataFrame(selected)


def entry_fill(path: dict, candidate, latency_ms: int, ttl_s: int, queue: str, size: float) -> tuple[int | None, float | None]:
    signal_ns = pd.Timestamp(candidate.cluster_100ms).value
    side = int(candidate.side)
    signal_q = int(np.searchsorted(path["q_ns"], signal_ns, side="right") - 1)
    limit = float(path["bid"][signal_q] if side > 0 else path["ask"][signal_q])
    arrival = signal_ns + int(latency_ms * 1e6)
    arrival_q = qindex_after(path["q_ns"], arrival)
    if arrival_q is None:
        return None, None
    same = np.isclose(path["bid"][arrival_q] if side > 0 else path["ask"][arrival_q], limit)
    nonmarketable = limit < path["ask"][arrival_q] if side > 0 else limit > path["bid"][arrival_q]
    if not same or not nonmarketable:
        return None, None
    displayed = float(path["bid_size"][arrival_q] if side > 0 else path["ask_size"][arrival_q])
    required = displayed + size if queue == "base" else 2 * displayed + size
    start = int(np.searchsorted(path["t_ns"], arrival, side="left"))
    end = int(np.searchsorted(path["t_ns"], arrival + int(ttl_s * 1e9), side="right"))
    prices = path["trade_price"][start:end]
    volumes = path["trade_size"][start:end]
    eligible = prices <= limit if side > 0 else prices >= limit
    cumulative = np.cumsum(np.where(eligible, volumes, 0.0))
    locations = np.flatnonzero(cumulative >= required)
    return (int(path["t_ns"][start + locations[0]]), limit) if len(locations) else (None, None)


def outcome(path: dict, candidate, latency: int, ttl: int, queue: str, size: float, horizon: int, slippage: int) -> dict | None:
    fill_ns, entry = entry_fill(path, candidate, latency, ttl, queue, size)
    if fill_ns is None:
        return None
    side = int(candidate.side)
    target_ns = fill_ns + int(horizon * 1e9)
    q0 = int(np.searchsorted(path["q_ns"], fill_ns, side="left"))
    q1 = int(np.searchsorted(path["q_ns"], target_ns, side="right"))
    mids = (path["bid"][q0:q1] + path["ask"][q0:q1]) / 2
    stop = mids <= entry * (1 - STOP_BP / 10000) if side > 0 else mids >= entry * (1 + STOP_BP / 10000)
    locations = np.flatnonzero(stop)
    exit_ns = int(path["q_ns"][q0 + locations[0]]) if len(locations) else target_ns
    q_exit = qindex_after(path["q_ns"], exit_ns)
    if q_exit is None:
        return None
    raw_exit = float(path["bid"][q_exit] if side > 0 else path["ask"][q_exit])
    slip = slippage / 10000
    exit_price = raw_exit * (1 - slip) if side > 0 else raw_exit * (1 + slip)
    net = side * (exit_price - entry) / entry - 2 * COMMISSION / entry
    return {"entry_ns": fill_ns, "exit_ns": exit_ns, "side": side, "entry_price": entry, "exit_price": exit_price, "net_return": net, "stopped": bool(len(locations))}


def evaluate(candidates: pd.DataFrame, paths: dict, sessions: list[str], latency: int, ttl: int, queue: str, size: float, horizon: int, slippage: int) -> tuple[dict, pd.DataFrame]:
    rows = []
    for candidate in candidates.itertuples():
        result = outcome(paths[(candidate.symbol, candidate.session, candidate.window)], candidate, latency, ttl, queue, size, horizon, slippage)
        if result is not None:
            rows.append({"symbol": candidate.symbol, "session": candidate.session, "signal_ts": candidate.cluster_100ms, **result})
    ledger = pd.DataFrame(rows)
    if ledger.empty:
        return {"fills": 0, "net": 0.0, "first_block": 0.0, "second_block": 0.0}, ledger
    ledger = ledger.sort_values("entry_ns")
    active, accepted = [], []
    for row in ledger.itertuples():
        active = [value for value in active if value > row.entry_ns]
        ok = len(active) < MAX_SLOTS
        accepted.append(ok)
        if ok:
            active.append(row.exit_ns)
    ledger = ledger[np.asarray(accepted)].copy()
    ledger["fixed_base_pnl"] = SLOT_WEIGHT * ledger.net_return
    daily = ledger.groupby("session").fixed_base_pnl.sum().reindex(sessions, fill_value=0.0)
    split = len(sessions) // 2
    return {
        "fills": len(ledger), "net": float(daily.sum()),
        "first_block": float(daily.iloc[:split].sum()), "second_block": float(daily.iloc[split:].sum()),
        "positive_day_fraction": float((daily > 0).mean()), "mean_trade_bp": float(ledger.net_return.mean() * 10000),
        "stop_rate": float(ledger.stopped.mean()),
    }, ledger


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    package = load_package()
    cuts = training_cuts(package)
    all_candidates, paths = enrich_candidates(package)
    candidate_sets = {}
    for quantile, cut in cuts.items():
        for spread in SPREAD_CENTS:
            candidate_sets[(quantile, spread)] = thin(all_candidates[(all_candidates.session.isin(DEV)) & (all_candidates.predicted_toxicity <= cut) & (all_candidates.spread_cents >= spread)])
    rows = []
    for (quantile, spread), candidates in candidate_sets.items():
        for size in SIZES:
            for latency in LATENCIES:
                for ttl in TTLS:
                    for queue in QUEUES:
                        for horizon in EXITS:
                            for slippage in SLIPPAGES:
                                result, _ = evaluate(candidates, paths, DEV, latency, ttl, queue, size, horizon, slippage)
                                rows.append({"toxicity_quantile": quantile, "spread_cents": spread, "order_size": size, "latency_ms": latency, "ttl_s": ttl, "queue_model": queue, "exit_horizon_s": horizon, "slippage_bp": slippage, "candidate_orders": len(candidates), **result})
    grid = pd.DataFrame(rows)
    grid.to_csv(OUT / "development_grid.csv", index=False)
    eligible = grid[(grid.fills >= 20) & (grid.first_block > 0) & (grid.second_block > 0)].copy()
    selected = None
    validation_metrics = None
    validation_ledger = pd.DataFrame()
    if len(eligible):
        eligible["worst_block"] = eligible[["first_block", "second_block"]].min(axis=1)
        eligible["queue_preference"] = (eligible.queue_model == "conservative").astype(int)
        selected = eligible.sort_values(["worst_block", "queue_preference", "order_size"], ascending=[False, False, True]).iloc[0]
        late_candidates = thin(all_candidates[(all_candidates.session.isin(VALIDATION)) & (all_candidates.predicted_toxicity <= cuts[selected.toxicity_quantile]) & (all_candidates.spread_cents >= selected.spread_cents)])
        validation_metrics, validation_ledger = evaluate(late_candidates, paths, VALIDATION, int(selected.latency_ms), int(selected.ttl_s), selected.queue_model, float(selected.order_size), int(selected.exit_horizon_s), int(selected.slippage_bp))
        pd.DataFrame([selected]).to_csv(OUT / "selected_development_config.csv", index=False)
        validation_ledger.to_csv(OUT / "validation_ledger.csv", index=False)
    report = {
        "status": "completed", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frozen_training_cuts": {str(key): value for key, value in cuts.items()},
        "planned_development_rows": 1296, "executed_development_rows": len(grid),
        "development_survivors": len(eligible),
        "selected_development_config": selected.to_dict() if selected is not None else None,
        "untouched_validation": validation_metrics,
        "validation_pass": bool(validation_metrics is not None and validation_metrics["net"] > 0 and validation_metrics["fills"] >= 20),
        "maximum_loaded_session": max(CONFIRMATION), "holdout_rows_loaded": 0,
        "decision_gate": "promising_execution_repair" if validation_metrics is not None and validation_metrics["net"] > 0 and validation_metrics["fills"] >= 20 else "execution_repair_failed",
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
