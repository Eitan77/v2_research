from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from run0002_toxicity_signal import CACHE, HORIZONS, SYMBOLS, WINDOWS, prior_values
from run0005_confirmation import CONFIRMATION, confirmation_clusters, load_package


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0631" / "artifacts" / "RUN-0006"
LATENCIES_MS = [50, 250, 500]
ENTRY_TTLS = [1, 2, 5, 10, 15]
QUEUE_MODELS = ["optimistic_diagnostic", "base", "conservative", "adversarial"]
EXIT_TTLS = [5, 15, 30]
COMMISSIONS = [0.0025, 0.0040]
SLIPPAGES_BP = [0, 1, 2]
ORDER_SIZE = 100.0
SLOT_WEIGHT = 0.05
MAX_SLOTS = 20
STOP_BP = 15
MAX_HOLD_S = 60
COOLDOWN_S = 60


def score_candidates() -> pd.DataFrame:
    package = load_package()
    data, _ = confirmation_clusters()
    features = package["features"]
    x = np.c_[np.ones(len(data)), (data[features].to_numpy() - np.asarray(package["mean"])) / np.asarray(package["std"])]
    data["predicted_toxicity"] = 1 / (1 + np.exp(-np.clip(x @ np.asarray(package["beta_intercept_then_features"]), -30, 30)))
    low = data[data.predicted_toxicity <= package["training_low_cut"]].sort_values(["session", "symbol", "cluster_100ms"])
    selected = []
    for _, group in low.groupby(["session", "symbol", "window"]):
        last = None
        for row in group.itertuples():
            if last is None or (row.cluster_100ms - last).total_seconds() >= COOLDOWN_S:
                selected.append(row._asdict())
                last = row.cluster_100ms
    return pd.DataFrame(selected)


def load_path(symbol: str, session: str, window: str) -> dict:
    q = pd.read_parquet(CACHE / "quotes" / f"{session}_{window}_{symbol}.parquet")
    t = pd.read_parquet(CACHE / "trades" / f"{session}_{window}_{symbol}.parquet")
    q.ts = pd.to_datetime(q.ts, utc=True)
    t.ts = pd.to_datetime(t.ts, utc=True)
    q = q.drop_duplicates("ts", keep="last").sort_values("ts")
    t = t.sort_values("ts")
    return {
        "q_ns": q.ts.astype("int64").to_numpy(),
        "bid": q.bid.to_numpy(float), "ask": q.ask.to_numpy(float),
        # SIP displayed sizes are round-lot units for these NMS quote records.
        "bid_size": q.bid_size.to_numpy(float) * 100.0,
        "ask_size": q.ask_size.to_numpy(float) * 100.0,
        "t_ns": t.ts.astype("int64").to_numpy(),
        "trade_price": t.price.to_numpy(float), "trade_size": t["size"].to_numpy(float),
    }


def qindex_after(q_ns: np.ndarray, target: int) -> int | None:
    index = int(np.searchsorted(q_ns, target, side="left"))
    return index if index < len(q_ns) else None


def passive_fill(path: dict, order_side: int, limit: float, arrival_ns: int, deadline_ns: int, displayed_ahead: float, queue_model: str) -> tuple[int | None, float]:
    start = int(np.searchsorted(path["t_ns"], arrival_ns, side="left"))
    end = int(np.searchsorted(path["t_ns"], deadline_ns, side="right"))
    prices = path["trade_price"][start:end]
    sizes = path["trade_size"][start:end]
    times = path["t_ns"][start:end]
    if queue_model == "adversarial":
        eligible = prices < limit if order_side > 0 else prices > limit
        locations = np.flatnonzero(eligible)
        return (int(times[locations[0]]), ORDER_SIZE) if len(locations) else (None, 0.0)
    eligible = prices <= limit if order_side > 0 else prices >= limit
    required = ORDER_SIZE
    if queue_model == "base":
        required += displayed_ahead
    elif queue_model == "conservative":
        required += 2 * displayed_ahead
    qualifying = np.where(eligible, sizes, 0.0)
    cumulative = np.cumsum(qualifying)
    locations = np.flatnonzero(cumulative >= required)
    if not len(locations):
        return None, float(min(ORDER_SIZE, cumulative[-1] if len(cumulative) else 0.0))
    return int(times[locations[0]]), ORDER_SIZE


def simulate_one(candidate, path: dict, latency_ms: int, entry_ttl: int, queue_model: str, exit_ttl: int) -> dict:
    signal_ns = pd.Timestamp(candidate.cluster_100ms).value
    side = int(candidate.side)
    signal_q = int(np.searchsorted(path["q_ns"], signal_ns, side="right") - 1)
    if signal_q < 0:
        return {"status": "no_signal_quote"}
    limit = float(path["bid"][signal_q] if side > 0 else path["ask"][signal_q])
    arrival_ns = signal_ns + int(latency_ms * 1e6)
    arrival_q = qindex_after(path["q_ns"], arrival_ns)
    if arrival_q is None:
        return {"status": "no_arrival_quote"}
    same_best = np.isclose(path["bid"][arrival_q] if side > 0 else path["ask"][arrival_q], limit)
    nonmarketable = limit < path["ask"][arrival_q] if side > 0 else limit > path["bid"][arrival_q]
    if not same_best or not nonmarketable:
        return {"status": "stale_or_marketable_at_arrival"}
    displayed = float(path["bid_size"][arrival_q] if side > 0 else path["ask_size"][arrival_q])
    fill_ns, partial = passive_fill(path, side, limit, arrival_ns, arrival_ns + int(entry_ttl * 1e9), displayed, queue_model)
    if fill_ns is None:
        return {"status": "entry_unfilled", "entry_partial_shares": partial}
    fill_q = qindex_after(path["q_ns"], fill_ns)
    if fill_q is None:
        return {"status": "no_fill_quote"}
    mid_fill = (path["bid"][fill_q] + path["ask"][fill_q]) / 2
    target = float(path["ask"][fill_q] if side > 0 else path["bid"][fill_q])
    exit_order_side = -side
    exit_arrival = fill_ns + int(latency_ms * 1e6)
    exit_q = qindex_after(path["q_ns"], exit_arrival)
    passive_exit_ns = None
    if exit_q is not None:
        exit_same_best = np.isclose(path["ask"][exit_q] if side > 0 else path["bid"][exit_q], target)
        if exit_same_best:
            exit_displayed = float(path["ask_size"][exit_q] if side > 0 else path["bid_size"][exit_q])
            passive_exit_ns, _ = passive_fill(path, exit_order_side, target, exit_arrival, exit_arrival + int(exit_ttl * 1e9), exit_displayed, queue_model)
    hard_ns = fill_ns + int(MAX_HOLD_S * 1e9)
    q_start = int(np.searchsorted(path["q_ns"], fill_ns, side="left"))
    q_end = int(np.searchsorted(path["q_ns"], hard_ns, side="right"))
    mids = (path["bid"][q_start:q_end] + path["ask"][q_start:q_end]) / 2
    stopped = mids <= limit * (1 - STOP_BP / 10000) if side > 0 else mids >= limit * (1 + STOP_BP / 10000)
    stop_locs = np.flatnonzero(stopped)
    stop_ns = int(path["q_ns"][q_start + stop_locs[0]]) if len(stop_locs) else None
    if passive_exit_ns is not None and (stop_ns is None or passive_exit_ns <= stop_ns):
        exit_ns, exit_price, exit_type = passive_exit_ns, target, "passive"
    else:
        exit_ns = stop_ns if stop_ns is not None else hard_ns
        force_q = qindex_after(path["q_ns"], exit_ns)
        if force_q is None:
            return {"status": "forced_exit_quote_missing"}
        exit_price = float(path["bid"][force_q] if side > 0 else path["ask"][force_q])
        exit_type = "stop" if stop_ns is not None else "max_hold"
    mark_q = qindex_after(path["q_ns"], fill_ns + int(5e9))
    mid5 = (path["bid"][mark_q] + path["ask"][mark_q]) / 2 if mark_q is not None else np.nan
    return {
        "status": "filled",
        "entry_ns": fill_ns,
        "exit_ns": exit_ns,
        "side": side,
        "entry_price": limit,
        "exit_price_raw": exit_price,
        "exit_type": exit_type,
        "spread_edge_bps": side * (mid_fill - limit) / mid_fill * 10000,
        "markout_5s_bps": side * (mid5 - mid_fill) / mid_fill * 10000,
    }


def portfolio_metrics(ledger: pd.DataFrame, commission: float, slippage_bp: int, sessions: list[str]) -> tuple[dict, pd.DataFrame]:
    filled = ledger[ledger.status == "filled"].sort_values("entry_ns").copy()
    active_exits = []
    accepted = []
    for row in filled.itertuples():
        active_exits = [value for value in active_exits if value > row.entry_ns]
        if len(active_exits) >= MAX_SLOTS:
            accepted.append(False)
        else:
            accepted.append(True)
            active_exits.append(row.exit_ns)
    filled["portfolio_accepted"] = accepted
    filled = filled[filled.portfolio_accepted].copy()
    slip = slippage_bp / 10000
    forced = filled.exit_type != "passive"
    filled["exit_price"] = np.where(forced & (filled.side > 0), filled.exit_price_raw * (1 - slip), np.where(forced & (filled.side < 0), filled.exit_price_raw * (1 + slip), filled.exit_price_raw))
    filled["gross_return"] = filled.side * (filled.exit_price_raw - filled.entry_price) / filled.entry_price
    filled["net_return"] = filled.side * (filled.exit_price - filled.entry_price) / filled.entry_price - 2 * commission / filled.entry_price
    filled["fixed_base_pnl"] = SLOT_WEIGHT * filled.net_return
    daily = pd.DataFrame({"session": sessions}).merge(filled.groupby("session").fixed_base_pnl.sum().rename("net_return"), on="session", how="left").fillna({"net_return": 0.0})
    equity = 1 + daily.net_return.cumsum()
    dd = (equity.cummax() - equity) / equity.cummax()
    metrics = {
        "orders": len(ledger), "fills": int((ledger.status == "filled").sum()), "portfolio_fills": len(filled),
        "fill_rate": float((ledger.status == "filled").mean()),
        "passive_exit_rate": float((filled.exit_type == "passive").mean()) if len(filled) else 0.0,
        "forced_exit_rate": float((filled.exit_type != "passive").mean()) if len(filled) else 0.0,
        "stop_rate": float((filled.exit_type == "stop").mean()) if len(filled) else 0.0,
        "mean_spread_edge_bps": float(filled.spread_edge_bps.mean()) if len(filled) else np.nan,
        "mean_markout_5s_bps": float(filled.markout_5s_bps.mean()) if len(filled) else np.nan,
        "mean_net_trade_bps": float(filled.net_return.mean() * 10000) if len(filled) else np.nan,
        "net_fixed_base_return": float(daily.net_return.sum()),
        "positive_day_fraction": float((daily.net_return > 0).mean()),
        "max_drawdown": float(dd.max()),
        "worst_day": float(daily.net_return.min()),
    }
    return metrics, filled


def semantic_fixtures() -> None:
    path = {"t_ns": np.array([1, 2, 3]), "trade_price": np.array([100.0, 100.0, 100.0]), "trade_size": np.array([100.0, 100.0, 100.0])}
    assert passive_fill(path, 1, 100.0, 1, 3, 100.0, "base")[0] == 2
    assert passive_fill(path, 1, 100.0, 1, 3, 100.0, "conservative")[0] == 3
    assert passive_fill(path, 1, 100.0, 1, 3, 100.0, "adversarial")[0] is None


def main() -> None:
    semantic_fixtures()
    OUT.mkdir(parents=True, exist_ok=True)
    candidates = score_candidates()
    paths = {(symbol, session, window): load_path(symbol, session, window) for symbol, session, window in candidates[["symbol", "session", "window"]].drop_duplicates().itertuples(index=False, name=None)}
    grid_rows = []
    top_ledgers = []
    path_cache = {}
    for latency in LATENCIES_MS:
        for entry_ttl in ENTRY_TTLS:
            for queue_model in QUEUE_MODELS:
                for exit_ttl in EXIT_TTLS:
                    key = (latency, entry_ttl, queue_model, exit_ttl)
                    records = []
                    for candidate in candidates.itertuples():
                        result = simulate_one(candidate, paths[(candidate.symbol, candidate.session, candidate.window)], latency, entry_ttl, queue_model, exit_ttl)
                        records.append({"symbol": candidate.symbol, "session": candidate.session, "window": candidate.window, "signal_ts": candidate.cluster_100ms, **result})
                    ledger = pd.DataFrame(records)
                    path_cache[key] = ledger
                    for commission in COMMISSIONS:
                        for slippage in SLIPPAGES_BP:
                            result, _ = portfolio_metrics(ledger, commission, slippage, CONFIRMATION)
                            grid_rows.append({"latency_ms": latency, "entry_ttl_s": entry_ttl, "queue_model": queue_model, "exit_ttl_s": exit_ttl, "commission_per_share_side": commission, "forced_slippage_bp": slippage, **result})
    grid = pd.DataFrame(grid_rows)
    grid.to_csv(OUT / "queue_grid.csv", index=False)
    eligible = grid[(grid.queue_model != "optimistic_diagnostic") & (grid.commission_per_share_side == 0.004) & (grid.forced_slippage_bp == 2)].sort_values("net_fixed_base_return", ascending=False)
    top = eligible.head(10)
    top.to_csv(OUT / "top_adverse_configs.csv", index=False)
    contribution_rows = []
    period_rows = []
    for row in top.itertuples():
        key = (row.latency_ms, row.entry_ttl_s, row.queue_model, row.exit_ttl_s)
        metrics, ledger = portfolio_metrics(path_cache[key], row.commission_per_share_side, row.forced_slippage_bp, CONFIRMATION)
        config = f"l{row.latency_ms}_e{row.entry_ttl_s}_{row.queue_model}_x{row.exit_ttl_s}"
        ledger["config"] = config
        top_ledgers.append(ledger)
        contribution_rows.extend({"config": config, "dimension": "symbol", "key": symbol, "trades": len(group), "net_fixed_base_return": float(group.fixed_base_pnl.sum())} for symbol, group in ledger.groupby("symbol"))
        contribution_rows.extend({"config": config, "dimension": "side", "key": "buy" if side > 0 else "short", "trades": len(group), "net_fixed_base_return": float(group.fixed_base_pnl.sum())} for side, group in ledger.groupby("side"))
        daily = ledger.groupby("session").fixed_base_pnl.sum().reindex(CONFIRMATION, fill_value=0.0)
        period_rows.extend({"config": config, "session": session, "net_return": float(value)} for session, value in daily.items())
    if top_ledgers:
        pd.concat(top_ledgers, ignore_index=True).to_csv(OUT / "top_trade_ledgers.csv", index=False)
    pd.DataFrame(contribution_rows).to_csv(OUT / "top_contribution.csv", index=False)
    pd.DataFrame(period_rows).to_csv(OUT / "top_session_paths.csv", index=False)
    report = {
        "status": "completed", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidate_orders_after_60s_cooldown": len(candidates),
        "planned_path_variants": len(LATENCIES_MS) * len(ENTRY_TTLS) * len(QUEUE_MODELS) * len(EXIT_TTLS),
        "executed_path_variants": len(path_cache),
        "planned_economic_rows": len(LATENCIES_MS) * len(ENTRY_TTLS) * len(QUEUE_MODELS) * len(EXIT_TTLS) * len(COMMISSIONS) * len(SLIPPAGES_BP),
        "executed_economic_rows": len(grid),
        "best_adverse_nonoptimistic": json.loads(top.head(5).to_json(orient="records")),
        "nonoptimistic_adverse_positive_count": int((eligible.net_fixed_base_return > 0).sum()),
        "maximum_loaded_session": max(CONFIRMATION), "holdout_rows_loaded": 0,
        "limitations": ["SIP queue proxy, not venue queue position", "displayed quote size treated as round-lot units", "historical borrow availability unavailable for short entries", "twelve sparse confirmation sessions, not a full-calendar PnL backtest"],
        "decision_gate": "profit_beyond_optimistic_with_distributed_contribution_required",
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
