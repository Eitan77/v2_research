from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ar_pipeline.data import _rth_sql_predicates
from ar_pipeline.data import _session_name
from ar_pipeline.data import connect_catalog
from ar_pipeline.data import load_research_matrix
from ar_pipeline.engines.cuda_discovery import summarize_cost_sensitivity
from ar_pipeline.engines.cuda_discovery import write_report
from ar_pipeline.execution import WorkloadInfo


def estimate_workload(config: dict[str, Any]) -> WorkloadInfo:
    scan = config.get("scan", {})
    specs = _build_specs(scan)
    costs = scan.get("cost_bps_per_side_grid", [scan.get("cost_bps_per_side", 5.0)])
    return WorkloadInfo(
        pattern="event_gated_intraday_reversal",
        preferred_device="cpu",
        supports_cuda=False,
        supports_cpu=True,
        supports_batch_autotune=False,
        estimated_rows=_estimate_rows(config),
        estimated_candidates=len(specs) * len(costs),
    )


def run(config: dict[str, Any], output_dir: Path) -> dict[str, str]:
    t0 = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "progress.log"
    scan = config.get("scan", {})
    horizon = int(scan.get("horizon", 1))
    label_col = f"fwd_return_{horizon}"
    cost_grid = [float(x) for x in scan.get("cost_bps_per_side_grid", [scan.get("cost_bps_per_side", 5.0)])]
    specs = _build_specs(scan)
    batch_size = int(scan.get("event_batch_size", 100))
    keep_trades_for = int(scan.get("keep_trades_for_top", 150))
    resume = bool(scan.get("resume", True))

    def log(message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with progress_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    df = load_research_matrix(config)
    if label_col not in df.columns:
        raise ValueError(f"Missing required label column {label_col!r}")
    df = df.copy()
    df["_session_minute"] = _session_minute(df["timestamp"])
    df["_event_day"] = df["timestamp"].dt.tz_convert("America/New_York").dt.date
    rank_features = sorted({s["feature"] for s in specs if s["kind"] == "rank"})
    for feature in rank_features:
        if feature not in df.columns:
            raise ValueError(f"Missing event feature {feature!r}")
        df[f"_rank_{feature}"] = df.groupby("timestamp", sort=False)[feature].rank(pct=True)

    total_batches = int(np.ceil(len(specs) / batch_size))
    batch_paths: list[Path] = []
    log(f"start event_reversal rows={len(df)} specs={len(specs)} batch_size={batch_size} cost_grid={cost_grid}")
    eval_t0 = time.perf_counter()
    for batch_idx, start in enumerate(range(0, len(specs), batch_size)):
        stop = min(start + batch_size, len(specs))
        batch_path = checkpoint_dir / f"batch_{batch_idx:05d}_{start}_{stop}.csv"
        if resume and _checkpoint_valid(batch_path):
            batch_paths.append(batch_path)
            elapsed = max(time.perf_counter() - eval_t0, 1e-9)
            rate = stop / elapsed
            remaining = len(specs) - stop
            eta = remaining / rate if rate > 0 else 0.0
            log(f"resume batch={batch_idx + 1}/{total_batches} specs={start}:{stop} eta_min={eta / 60:.1f}")
            continue
        rows: list[dict[str, Any]] = []
        for spec_id in range(start, stop):
            spec = specs[spec_id]
            trades = _select_trades(df, spec, label_col)
            for cost in cost_grid:
                rows.append(_metric_row(trades, spec, spec_id, horizon, cost, scan.get("family", "event_reversal")))
        batch_df = pd.DataFrame(rows)
        tmp = batch_path.with_suffix(batch_path.suffix + ".tmp")
        batch_df.to_csv(tmp, index=False)
        tmp.replace(batch_path)
        batch_paths.append(batch_path)
        elapsed = max(time.perf_counter() - eval_t0, 1e-9)
        rate = stop / elapsed
        remaining = len(specs) - stop
        eta = remaining / rate if rate > 0 else 0.0
        log(f"batch={batch_idx + 1}/{total_batches} specs={start}:{stop} done={stop}/{len(specs)} rate_specs_sec={rate:.2f} eta_min={eta / 60:.1f}")

    leaderboard = pd.concat((pd.read_csv(p) for p in sorted(batch_paths)), ignore_index=True)
    leaderboard = leaderboard.sort_values(["cagr", "log_total_return"], ascending=False).reset_index(drop=True)
    leaderboard.to_csv(output_dir / "leaderboard.csv", index=False)
    leaderboard.to_parquet(output_dir / "leaderboard.parquet", index=False)
    cost_summary = summarize_cost_sensitivity(leaderboard)
    cost_summary.to_csv(output_dir / "cost_sensitivity.csv", index=False)

    selected_base = _selected_trade_bases(leaderboard, cost_summary, keep_trades_for)
    trade_parts = []
    for candidate_id in selected_base["base_candidate_id"].astype(str):
        spec_id = int(candidate_id[1:])
        part = _select_trades(df, specs[spec_id], label_col).copy()
        if part.empty:
            continue
        part["candidate_id"] = candidate_id
        part["rank_formula_id"] = spec_id
        part["top_n"] = int(specs[spec_id]["max_positions"])
        part["discovery_cost_bps_per_side"] = 0.0
        trade_parts.append(part)
    trades = pd.concat(trade_parts, ignore_index=True) if trade_parts else pd.DataFrame()
    trades.to_parquet(output_dir / "discovery_trades.parquet", index=False)

    metadata = {
        "rows": int(len(df)),
        "features": sorted(set(scan.get("features", []))),
        "cost_bps_per_side_grid": cost_grid,
        "device": "cpu",
        "cuda_name": "",
        "load_elapsed_seconds": None,
        "eval_elapsed_seconds": round(time.perf_counter() - eval_t0, 3),
        "total_elapsed_seconds": round(time.perf_counter() - t0, 3),
        "event_specs": len(specs),
        "candidate_rows": int(len(leaderboard)),
        "checkpoint_batches": len(batch_paths),
        "checkpoint_dir": str(checkpoint_dir),
        "progress_log": str(progress_path),
        "formulas_per_second": None,
        "candidate_rows_per_second": None,
        "cuda_memory_peak_gb": 0.0,
        "config": config,
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "event_specs.json").write_text(json.dumps(specs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(output_dir, leaderboard, metadata)
    log(f"done event_reversal candidate_rows={len(leaderboard)} elapsed_sec={metadata['total_elapsed_seconds']}")
    return {
        "leaderboard": str(output_dir / "leaderboard.csv"),
        "leaderboard_parquet": str(output_dir / "leaderboard.parquet"),
        "cost_sensitivity": str(output_dir / "cost_sensitivity.csv"),
        "trades": str(output_dir / "discovery_trades.parquet"),
        "report": str(output_dir / "discovery_report.md"),
        "event_specs": str(output_dir / "event_specs.json"),
    }


def _build_specs(scan: dict[str, Any]) -> list[dict[str, Any]]:
    features = set(scan.get("features") or [])
    rank_features = [
        ("close_vs_vwap", "low", [0.01, 0.02, 0.05, 0.10]),
        ("session_open_return", "low", [0.01, 0.02, 0.05, 0.10]),
        ("bar_return", "low", [0.01, 0.02, 0.05, 0.10]),
        ("bb_percent_b_20_2", "low", [0.01, 0.02, 0.05, 0.10]),
        ("rsi_14", "low", [0.01, 0.02, 0.05, 0.10]),
        ("stoch_k_14", "low", [0.01, 0.02, 0.05, 0.10]),
        ("williams_r_14", "low", [0.01, 0.02, 0.05, 0.10]),
        ("cci_20", "low", [0.01, 0.02, 0.05, 0.10]),
        ("mfi_14", "low", [0.01, 0.02, 0.05, 0.10]),
        ("close_in_bar_range", "low", [0.01, 0.02, 0.05, 0.10]),
    ]
    contexts = [
        {},
        {"min_relative_volume": 1.5},
        {"min_relative_volume": 2.5},
        {"trend": "above_sma20"},
        {"trend": "below_sma20"},
        {"first_minutes": 90},
        {"after_minutes": 120},
        {"min_lower_wick": 0.4},
        {"max_bar_return": -0.005},
        {"max_session_open_return": -0.01},
    ]
    max_positions = [1, 2, 3]
    specs: list[dict[str, Any]] = []
    for feature, direction, qs in rank_features:
        if feature not in features:
            continue
        for q in qs:
            for ctx in contexts:
                for n in max_positions:
                    specs.append({"kind": "rank", "feature": feature, "direction": direction, "quantile": q, "max_positions": n, **ctx})
    if "rsi_14" in features:
        for level in [15, 20, 25, 30, 35]:
            for n in max_positions:
                specs.append({"kind": "absolute", "feature": "rsi_14", "op": "<=", "level": level, "max_positions": n, "rank_by": "rsi_14", "rank_ascending": True})
    if "bb_percent_b_20_2" in features:
        for level in [-0.2, 0.0, 0.05, 0.10, 0.20]:
            for n in max_positions:
                specs.append({"kind": "absolute", "feature": "bb_percent_b_20_2", "op": "<=", "level": level, "max_positions": n, "rank_by": "bb_percent_b_20_2", "rank_ascending": True})
    return specs


def _select_trades(df: pd.DataFrame, spec: dict[str, Any], label_col: str) -> pd.DataFrame:
    if spec["kind"] == "rank":
        rank = df[f"_rank_{spec['feature']}"]
        if spec["direction"] == "low":
            mask = rank <= float(spec["quantile"])
            ascending = True
        else:
            mask = rank >= 1.0 - float(spec["quantile"])
            ascending = False
        rank_by = spec["feature"]
    else:
        values = df[spec["feature"]]
        level = float(spec["level"])
        mask = values <= level if spec["op"] == "<=" else values >= level
        rank_by = spec.get("rank_by", spec["feature"])
        ascending = bool(spec.get("rank_ascending", True))
    if spec.get("min_relative_volume") is not None and "relative_volume_20" in df:
        mask &= df["relative_volume_20"] >= float(spec["min_relative_volume"])
    if spec.get("trend") == "above_sma20" and "close_vs_sma_20" in df:
        mask &= df["close_vs_sma_20"] >= 0
    if spec.get("trend") == "below_sma20" and "close_vs_sma_20" in df:
        mask &= df["close_vs_sma_20"] < 0
    if spec.get("first_minutes") is not None:
        mask &= df["_session_minute"] <= int(spec["first_minutes"])
    if spec.get("after_minutes") is not None:
        mask &= df["_session_minute"] >= int(spec["after_minutes"])
    if spec.get("min_lower_wick") is not None and "lower_wick_pct" in df:
        mask &= df["lower_wick_pct"] >= float(spec["min_lower_wick"])
    if spec.get("max_bar_return") is not None and "bar_return" in df:
        mask &= df["bar_return"] <= float(spec["max_bar_return"])
    if spec.get("max_session_open_return") is not None and "session_open_return" in df:
        mask &= df["session_open_return"] <= float(spec["max_session_open_return"])
    selected = df.loc[mask, ["symbol", "timestamp", "open", "high", "low", "close", label_col, rank_by]].copy()
    if selected.empty:
        return _empty_trades(label_col)
    selected = (
        selected.sort_values(["timestamp", rank_by], ascending=[True, ascending])
        .groupby("timestamp", sort=False)
        .head(int(spec["max_positions"]))
        .drop(columns=[rank_by])
        .copy()
    )
    selected["entry_ts"] = selected["timestamp"]
    horizon = int(label_col.rsplit("_", 1)[1])
    selected["exit_ts"] = selected["timestamp"] + pd.to_timedelta(horizon * 15, unit="m")
    gross = selected[label_col].astype(float)
    selected["entry_ref_price"] = selected["close"]
    selected["exit_ref_price"] = selected["close"] * (1.0 + gross)
    selected["source_return"] = gross
    selected["gross_source_return"] = gross
    return selected.dropna(subset=["source_return"])


def _metric_row(trades: pd.DataFrame, spec: dict[str, Any], spec_id: int, horizon: int, cost: float, family: str) -> dict[str, Any]:
    base_id = f"e{spec_id:06d}"
    candidate_id = f"{base_id}_c{str(cost).replace('.', 'p')}"
    if trades.empty:
        net = np.array([], dtype=np.float64)
    else:
        net = trades["source_return"].to_numpy(dtype=np.float64) - (2.0 * cost / 10000.0)
    metrics = _metrics(net, trades["timestamp"] if not trades.empty else None)
    return {
        "candidate_id": candidate_id,
        "base_candidate_id": base_id,
        "family": family,
        "formula_id": spec_id,
        "top_n": int(spec["max_positions"]),
        "horizon": horizon,
        "cost_bps_per_side": cost,
        "spec": json.dumps(spec, sort_keys=True),
        **metrics,
    }


def _selected_trade_bases(leaderboard: pd.DataFrame, cost_summary: pd.DataFrame, keep: int) -> pd.DataFrame:
    top_keep = max(10, keep // 3)
    survivor_keep = max(10, keep // 3)
    controlled_keep = max(10, keep - top_keep - survivor_keep)
    picks = []
    picks.append(leaderboard.sort_values(["cagr", "log_total_return"], ascending=False).drop_duplicates("base_candidate_id").head(top_keep))
    survivors = cost_summary[
        (cost_summary["max_profitable_cost_bps_per_side"] >= 2.0)
        & (cost_summary["trades"] >= 100)
    ].sort_values(["max_profitable_cost_bps_per_side", "best_cagr", "best_total_return"], ascending=False)
    if not survivors.empty:
        picks.append(leaderboard[leaderboard["base_candidate_id"].isin(survivors["base_candidate_id"].head(survivor_keep))].drop_duplicates("base_candidate_id"))
    controlled = leaderboard[
        (leaderboard["cost_bps_per_side"].isin([2.0, 5.0]))
        & (leaderboard["trades"] >= 100)
        & (leaderboard["max_drawdown"] >= -0.45)
        & (leaderboard["total_return"] > 0)
    ].sort_values(["cost_bps_per_side", "cagr"], ascending=[False, False])
    if not controlled.empty:
        picks.append(controlled.drop_duplicates("base_candidate_id").head(controlled_keep))
    selected = pd.concat(picks, ignore_index=True).drop_duplicates("base_candidate_id")
    return selected.head(keep)


def _metrics(returns: np.ndarray, timestamps: pd.Series | None) -> dict[str, Any]:
    if len(returns) == 0:
        return {"trades": 0.0, "win_rate": 0.0, "avg_return": 0.0, "log_total_return": 0.0, "total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0}
    clipped = np.clip(returns, -0.999999, None)
    log_rets = np.log1p(clipped)
    equity = np.exp(np.cumsum(log_rets))
    peak = np.maximum.accumulate(equity)
    dd = equity / np.maximum(peak, 1e-12) - 1.0
    if timestamps is not None and len(timestamps) > 1:
        days = max((pd.to_datetime(timestamps.max()) - pd.to_datetime(timestamps.min())).days, 1)
    else:
        days = 1
    log_total = float(log_rets.sum())
    total_return = float(np.exp(log_total) - 1.0) if log_total < 700 else float("inf")
    cagr = float(np.exp(log_total * 365.25 / days) - 1.0) if days > 0 and log_total < 700 else float("inf")
    return {
        "trades": float(len(returns)),
        "win_rate": float((returns > 0).mean()),
        "avg_return": float(returns.mean()),
        "log_total_return": log_total,
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": float(dd.min()),
    }


def _session_minute(ts: pd.Series) -> pd.Series:
    ny = ts.dt.tz_convert("America/New_York")
    return (ny.dt.hour - 9) * 60 + (ny.dt.minute - 30)


def _empty_trades(label_col: str) -> pd.DataFrame:
    return pd.DataFrame(columns=["symbol", "timestamp", "open", "high", "low", "close", label_col, "entry_ts", "exit_ts", "entry_ref_price", "exit_ref_price", "source_return", "gross_source_return"])


def _checkpoint_valid(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        cols = set(pd.read_csv(path, nrows=0).columns)
    except Exception:
        return False
    return {"candidate_id", "base_candidate_id", "cagr", "log_total_return", "cost_bps_per_side"}.issubset(cols)


def _estimate_rows(config: dict[str, Any]) -> int | None:
    data_cfg = config.get("data", {})
    scan = config.get("scan", {})
    table = data_cfg.get("table", "research_matrix")
    horizon = int(scan.get("horizon", 1))
    label_col = f"fwd_return_{horizon}"
    where = ["timeframe = ?"]
    params: list[Any] = [scan.get("timeframe", "15m")]
    if scan.get("train_start"):
        where.append("cast(timestamp as timestamp) >= ?")
        params.append(scan["train_start"])
    if scan.get("train_end"):
        where.append("cast(timestamp as timestamp) <= ?")
        params.append(scan["train_end"])
    if scan.get("universe") == "qqq_pit":
        where.append("coalesce(is_qqq_member, false)")
    if _session_name(scan) == "rth":
        where.extend(_rth_sql_predicates(horizon, str(scan.get("timeframe", "15m"))))
    where.append(f"{label_col} is not null")
    con = connect_catalog(data_cfg.get("catalog_path"))
    try:
        return int(con.execute(f"select count(*) from {table} where {' and '.join(where)}", params).fetchone()[0])
    finally:
        con.close()
