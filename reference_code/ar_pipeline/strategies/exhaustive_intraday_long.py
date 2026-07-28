from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ar_pipeline.data import _derived_feature_inputs, _market_holidays, add_derived_features, connect_catalog
from ar_pipeline.engines.cuda_discovery import summarize_cost_sensitivity, write_report
from ar_pipeline.execution import WorkloadInfo


ETF_SYMBOLS = {
    "QQQ", "SPY", "IWM", "DIA", "TQQQ", "SQQQ", "SOXL", "SOXS", "SMH", "XLK", "XLF",
    "XLE", "TLT", "GLD", "USO", "ARKK", "VOO", "IVV", "VIXY",
}


def estimate_workload(config: dict[str, Any]) -> WorkloadInfo:
    scan = config.get("scan", {})
    specs = _build_specs(scan)
    costs = scan.get("cost_bps_per_side_grid", [scan.get("cost_bps_per_side", 5.0)])
    return WorkloadInfo(
        pattern="exhaustive_intraday_long_family",
        preferred_device="cpu",
        supports_cuda=False,
        supports_cpu=True,
        supports_batch_autotune=False,
        estimated_rows=None,
        estimated_candidates=len(specs) * len(costs),
    )


def run(config: dict[str, Any], output_dir: Path) -> dict[str, str]:
    t0 = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "progress.log"
    scan = config.get("scan", {})
    specs = _build_specs(scan)
    costs = [float(x) for x in scan.get("cost_bps_per_side_grid", [0.0, 2.0, 5.0, 10.0, 25.0, 50.0])]
    resume = bool(scan.get("resume", True))
    keep_trades_for = int(scan.get("keep_trades_for_top", 250))
    spec_batch_size = int(scan.get("spec_batch_size", 200))

    def log(message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with progress_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    rows: list[pd.DataFrame] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for spec in specs:
        grouped.setdefault(spec["timeframe"], []).append(spec)

    done = 0
    total = len(specs)
    eval_t0 = time.perf_counter()
    log(f"start exhaustive_intraday_long specs={total} groups={len(grouped)} cost_grid={costs}")
    for group_idx, (timeframe, group_specs) in enumerate(sorted(grouped.items()), start=1):
        horizons = sorted({int(s["horizon"]) for s in group_specs})
        batch_ranges = list(enumerate(range(0, len(group_specs), spec_batch_size)))
        group_paths = [
            checkpoint_dir / f"group_{group_idx:03d}_{timeframe}_batch_{batch_idx:04d}_{start}_{min(start + spec_batch_size, len(group_specs))}.csv"
            for batch_idx, start in batch_ranges
        ]
        if resume and group_paths and all(_checkpoint_valid(path) for path in group_paths):
            rows.extend(pd.read_csv(path) for path in group_paths)
            done += len(group_specs)
            log(_progress("resume", group_idx, len(grouped), done, total, eval_t0, timeframe, horizons))
            continue
        rank_features = sorted({str(s["feature"]) for s in group_specs if str(s["selector"]).startswith("rank_")})
        df = _load_timeframe_frame(config, timeframe, horizons)
        df = _prepare_frame(df, horizons, rank_features)
        for batch_idx, start in batch_ranges:
            stop = min(start + spec_batch_size, len(group_specs))
            batch_specs = group_specs[start:stop]
            batch_path = checkpoint_dir / f"group_{group_idx:03d}_{timeframe}_batch_{batch_idx:04d}_{start}_{stop}.csv"
            if resume and _checkpoint_valid(batch_path):
                rows.append(pd.read_csv(batch_path))
                done += len(batch_specs)
                log(_progress("resume_batch", group_idx, len(grouped), done, total, eval_t0, timeframe, horizons))
                continue
            group_rows: list[dict[str, Any]] = []
            for spec in batch_specs:
                selected = _select(df, spec, f"fwd_return_{int(spec['horizon'])}")
                for cost in costs:
                    group_rows.append(_metric_row(selected, spec, cost))
            batch_df = pd.DataFrame(group_rows)
            tmp = batch_path.with_suffix(".csv.tmp")
            batch_df.to_csv(tmp, index=False)
            tmp.replace(batch_path)
            rows.append(batch_df)
            done += len(batch_specs)
            log(_progress("batch", group_idx, len(grouped), done, total, eval_t0, timeframe, horizons))

    leaderboard = pd.concat(rows, ignore_index=True)
    leaderboard = leaderboard.sort_values(["cagr", "log_total_return"], ascending=False).reset_index(drop=True)
    leaderboard.to_csv(output_dir / "leaderboard.csv", index=False)
    leaderboard.to_parquet(output_dir / "leaderboard.parquet", index=False)
    cost_summary = summarize_cost_sensitivity(leaderboard)
    cost_summary.to_csv(output_dir / "cost_sensitivity.csv", index=False)

    selected_bases = _select_trade_bases(leaderboard, cost_summary, keep_trades_for)
    trades = _rebuild_selected_trades(config, specs, selected_bases, log)
    trades.to_parquet(output_dir / "discovery_trades.parquet", index=False)

    metadata = {
        "rows": int(sum(x["trades"] for _, x in leaderboard.drop_duplicates("base_candidate_id").iterrows())),
        "features": sorted(set(scan.get("features", []))),
        "cost_bps_per_side_grid": costs,
        "device": "cpu",
        "cuda_name": "",
        "load_elapsed_seconds": None,
        "eval_elapsed_seconds": round(time.perf_counter() - eval_t0, 3),
        "total_elapsed_seconds": round(time.perf_counter() - t0, 3),
        "candidate_rows": int(len(leaderboard)),
        "checkpoint_batches": len(grouped),
        "checkpoint_dir": str(checkpoint_dir),
        "progress_log": str(progress_path),
        "formulas_per_second": None,
        "candidate_rows_per_second": None,
        "cuda_memory_peak_gb": 0.0,
        "config": config,
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "strategy_specs.json").write_text(json.dumps(specs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(output_dir, leaderboard, metadata)
    log(f"done exhaustive_intraday_long candidate_rows={len(leaderboard)} elapsed_sec={metadata['total_elapsed_seconds']}")
    return {
        "leaderboard": str(output_dir / "leaderboard.csv"),
        "leaderboard_parquet": str(output_dir / "leaderboard.parquet"),
        "cost_sensitivity": str(output_dir / "cost_sensitivity.csv"),
        "trades": str(output_dir / "discovery_trades.parquet"),
        "report": str(output_dir / "discovery_report.md"),
        "strategy_specs": str(output_dir / "strategy_specs.json"),
    }


def _build_specs(scan: dict[str, Any]) -> list[dict[str, Any]]:
    timeframes = scan.get("timeframes", ["5m", "10m", "15m", "30m", "1h"])
    horizons_by_tf = scan.get("horizons_by_timeframe", {
        "5m": [1, 2, 3, 6, 12, 24],
        "10m": [1, 2, 3, 6, 12],
        "15m": [1, 2, 4, 6, 12],
        "30m": [1, 2, 4, 6],
        "1h": [1, 2, 3, 4],
    })
    selectors = scan.get("selectors") or [
        ("rank_low", "close_vs_vwap", [0.01, 0.02, 0.05, 0.10, 0.20]),
        ("rank_low", "session_open_return", [0.01, 0.02, 0.05, 0.10, 0.20]),
        ("rank_low", "bar_return", [0.01, 0.02, 0.05, 0.10, 0.20]),
        ("rank_low", "close_in_bar_range", [0.01, 0.02, 0.05, 0.10, 0.20]),
        ("rank_low", "rsi_14", [0.01, 0.02, 0.05, 0.10, 0.20]),
        ("rank_low", "stoch_k_14", [0.01, 0.02, 0.05, 0.10, 0.20]),
        ("rank_low", "williams_r_14", [0.01, 0.02, 0.05, 0.10, 0.20]),
        ("rank_low", "bb_percent_b_20_2", [0.01, 0.02, 0.05, 0.10, 0.20]),
        ("rank_high", "hl_range_pct", [0.01, 0.02, 0.05, 0.10]),
        ("rank_high", "relative_volume_20", [0.01, 0.02, 0.05, 0.10]),
        ("abs_le", "rsi_14", [15, 20, 25, 30, 35]),
        ("abs_le", "bb_percent_b_20_2", [-0.2, 0.0, 0.05, 0.10, 0.20]),
        ("abs_le", "session_open_return", [-0.05, -0.03, -0.02, -0.01]),
        ("abs_le", "bar_return", [-0.02, -0.01, -0.005]),
    ]
    windows = scan.get("windows") or [
        ("open_30", 0, 30),
        ("first_90", 0, 90),
        ("midday", 90, 240),
        ("power_hour", 300, 390),
        ("all_day", 0, 390),
    ]
    contexts = scan.get("contexts") or [
        ("plain", {}),
        ("high_rvol", {"min_relative_volume": 1.5}),
        ("very_high_rvol", {"min_relative_volume": 2.5}),
        ("above_sma20", {"trend": "above_sma20"}),
        ("below_sma20", {"trend": "below_sma20"}),
        ("lower_wick", {"min_lower_wick": 0.35}),
        ("price20", {"min_price": 20.0}),
        ("price50", {"min_price": 50.0}),
    ]
    universe_modes = scan.get("universe_modes") or ["all", "qqq_pit", "etf"]
    max_positions_by_universe = scan.get("max_positions_by_universe") or {
        "all": [1, 2, 3, 5, 10],
        "qqq_pit": [1, 2, 3, 5, 10],
        "etf": [1, 2, 3],
        "single_symbol": [1],
        "exact_symbol": [1],
    }
    exact_symbols = [str(x).upper() for x in scan.get("exact_symbols", [])]
    exact_symbol_selectors = scan.get("exact_symbol_selectors") or selectors
    exact_symbol_windows = scan.get("exact_symbol_windows") or windows
    exact_symbol_contexts = scan.get("exact_symbol_contexts") or [("plain", {})]
    specs: list[dict[str, Any]] = []
    spec_id = 0
    for timeframe in timeframes:
        for horizon in horizons_by_tf.get(timeframe, []):
            for selector, feature, levels in selectors:
                for level in levels:
                    for window_name, start_min, end_min in windows:
                        for context_name, context in contexts:
                            for universe in universe_modes:
                                ns = [int(x) for x in max_positions_by_universe.get(universe, [1])]
                                for n in ns:
                                    specs.append({
                                        "spec_id": spec_id,
                                        "timeframe": timeframe,
                                        "horizon": int(horizon),
                                        "selector": selector,
                                        "feature": feature,
                                        "level": float(level),
                                        "window": window_name,
                                        "start_minute": int(start_min),
                                        "end_minute": int(end_min),
                                        "context": context_name,
                                        "universe_mode": universe,
                                        "max_positions": int(n),
                                        **context,
                                    })
                                    spec_id += 1
            if exact_symbols:
                for selector, feature, levels in exact_symbol_selectors:
                    for level in levels:
                        for window_name, start_min, end_min in exact_symbol_windows:
                            for context_name, context in exact_symbol_contexts:
                                for symbol in exact_symbols:
                                    specs.append({
                                        "spec_id": spec_id,
                                        "timeframe": timeframe,
                                        "horizon": int(horizon),
                                        "selector": selector,
                                        "feature": feature,
                                        "level": float(level),
                                        "window": window_name,
                                        "start_minute": int(start_min),
                                        "end_minute": int(end_min),
                                        "context": context_name,
                                        "universe_mode": "exact_symbol",
                                        "symbol": symbol,
                                        "max_positions": 1,
                                        **context,
                                    })
                                    spec_id += 1
    max_specs = scan.get("max_specs")
    if max_specs is not None and int(max_specs) > 0 and len(specs) > int(max_specs):
        specs = _stratified_specs(specs, int(max_specs))
        for i, spec in enumerate(specs):
            spec["spec_id"] = i
    return specs


def _stratified_specs(specs: list[dict[str, Any]], max_specs: int) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for spec in specs:
        key = (str(spec["timeframe"]), str(spec["universe_mode"]), str(spec["selector"]))
        groups.setdefault(key, []).append(spec)
    selected: list[dict[str, Any]] = []
    quota = max(1, max_specs // max(len(groups), 1))
    for key in sorted(groups):
        group = groups[key]
        take = min(len(group), quota)
        if take == len(group):
            selected.extend(group)
        else:
            idx = np.linspace(0, len(group) - 1, take, dtype=int)
            selected.extend(group[int(i)] for i in idx)
    if len(selected) < max_specs:
        chosen = {id(x) for x in selected}
        remaining = [x for x in specs if id(x) not in chosen]
        need = min(max_specs - len(selected), len(remaining))
        if need > 0:
            idx = np.linspace(0, len(remaining) - 1, need, dtype=int)
            selected.extend(remaining[int(i)] for i in idx)
    return selected[:max_specs]


def _load_timeframe_frame(config: dict[str, Any], timeframe: str, horizons: list[int]) -> pd.DataFrame:
    data_cfg = config.get("data", {})
    scan = config.get("scan", {})
    features = list(scan.get("features") or [])
    derived = _derived_feature_inputs(features)
    labels = [f"fwd_return_{h}" for h in horizons]
    required = ["symbol", "timestamp", "timeframe", "open", "high", "low", "close", "volume", "is_qqq_member"]
    columns = list(dict.fromkeys(required + labels + [f for f in features if f not in derived] + list(derived.values())))
    con = connect_catalog(data_cfg.get("catalog_path"))
    try:
        available = {r[0] for r in con.execute(f"describe {data_cfg.get('table', 'research_matrix')}").fetchall()}
        missing = [c for c in columns if c not in available]
        if missing:
            raise ValueError(f"research_matrix is missing required columns: {missing}")
        where = ["timeframe = ?"]
        params: list[Any] = [timeframe]
        if scan.get("train_start"):
            where.append("cast(timestamp as timestamp) >= ?")
            params.append(scan["train_start"])
        if scan.get("train_end"):
            where.append("cast(timestamp as timestamp) <= ?")
            params.append(scan["train_end"])
        local_entry = "(cast(timestamp as timestamptz) at time zone 'America/New_York')"
        where.extend([
            f"extract(dow from {local_entry}) between 1 and 5",
            f"(extract(hour from {local_entry}) * 60 + extract(minute from {local_entry})) >= 570",
            f"(extract(hour from {local_entry}) * 60 + extract(minute from {local_entry})) <= 960",
        ])
        non_null = " or ".join(f"{label} is not null" for label in labels)
        sql = f"""
            select {', '.join(columns)}
            from {data_cfg.get('table', 'research_matrix')}
            where {' and '.join(where)}
              and ({non_null})
            qualify row_number() over (
              partition by symbol,timestamp,timeframe
              order by timestamp
            ) = 1
            order by timestamp, symbol
        """
        df = con.execute(sql, params).fetchdf()
    finally:
        con.close()
    if df.empty:
        raise ValueError(f"scan query returned no rows for timeframe={timeframe}")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="mixed")
    df = add_derived_features(df, features)
    return df


def _prepare_frame(df: pd.DataFrame, horizons: list[int], rank_features: list[str]) -> pd.DataFrame:
    out = df.copy()
    local = out["timestamp"].dt.tz_convert("America/New_York")
    out["_session_minute"] = (local.dt.hour * 60 + local.dt.minute) - (9 * 60 + 30)
    out["_is_etf"] = out["symbol"].astype(str).isin(ETF_SYMBOLS)
    entry_date = local.dt.normalize().dt.tz_localize(None)
    start = entry_date.min() - pd.Timedelta(days=7)
    end = entry_date.max() + pd.Timedelta(days=7)
    holidays = _market_holidays(start, end)
    for horizon in horizons:
        exit_ts = out["timestamp"] + pd.to_timedelta(_tf_minutes(str(out["timeframe"].iloc[0])) * horizon, unit="m")
        exit_local = exit_ts.dt.tz_convert("America/New_York")
        exit_date = exit_local.dt.normalize().dt.tz_localize(None)
        exit_minutes = exit_local.dt.hour * 60 + exit_local.dt.minute
        out[f"_horizon_ok_{horizon}"] = (
            entry_date.eq(exit_date)
            & ~entry_date.isin(holidays)
            & ~exit_date.isin(holidays)
            & exit_minutes.le(16 * 60)
            & out[f"fwd_return_{horizon}"].notna()
        )
    for feature in rank_features:
        if feature in out.columns:
            out[f"_rank_{feature}"] = out.groupby("timestamp", sort=False)[feature].rank(pct=True)
    return out


def _select(df: pd.DataFrame, spec: dict[str, Any], label_col: str) -> pd.DataFrame:
    horizon = int(spec["horizon"])
    d = df[
        (df[f"_horizon_ok_{horizon}"])
        & (df["_session_minute"] >= spec["start_minute"])
        & (df["_session_minute"] <= spec["end_minute"])
    ].copy()
    if spec["universe_mode"] == "qqq_pit":
        d = d[d["is_qqq_member"].fillna(False)]
    elif spec["universe_mode"] == "etf":
        d = d[d["_is_etf"]]
    elif spec["universe_mode"] == "exact_symbol":
        d = d[d["symbol"].astype(str).eq(str(spec["symbol"]))]
    if spec.get("min_price") is not None:
        d = d[d["close"].astype(float) >= float(spec["min_price"])]
    if spec.get("min_relative_volume") is not None and "relative_volume_20" in d:
        d = d[d["relative_volume_20"].astype(float) >= float(spec["min_relative_volume"])]
    if spec.get("trend") == "above_sma20" and "close_vs_sma_20" in d:
        d = d[d["close_vs_sma_20"].astype(float) >= 0]
    if spec.get("trend") == "below_sma20" and "close_vs_sma_20" in d:
        d = d[d["close_vs_sma_20"].astype(float) < 0]
    if spec.get("min_lower_wick") is not None and "lower_wick_pct" in d:
        d = d[d["lower_wick_pct"].astype(float) >= float(spec["min_lower_wick"])]
    if d.empty or spec["feature"] not in d:
        return _empty(label_col)
    feature = spec["feature"]
    selector = spec["selector"]
    if selector.startswith("rank_"):
        rank_col = f"_rank_{feature}"
        if rank_col not in d:
            return _empty(label_col)
        pct = d[rank_col]
        if selector == "rank_low":
            d = d[pct <= float(spec["level"])]
            ascending = True
        else:
            d = d[pct >= 1.0 - float(spec["level"])]
            ascending = False
        if d.empty:
            return _empty(label_col)
        if spec["universe_mode"] in {"single_symbol", "exact_symbol"}:
            selected = d.sort_values(["symbol", "timestamp", feature], ascending=[True, True, ascending]).groupby(["symbol", "timestamp"], sort=False).head(1)
        else:
            selected = d.sort_values(["timestamp", feature], ascending=[True, ascending]).groupby("timestamp", sort=False).head(int(spec["max_positions"]))
    elif selector == "abs_le":
        d = d[d[feature].astype(float) <= float(spec["level"])]
        if d.empty:
            return _empty(label_col)
        selected = d.sort_values(["timestamp", feature], ascending=[True, True]).groupby("timestamp", sort=False).head(int(spec["max_positions"]))
    else:
        raise ValueError(f"Unknown selector {selector}")
    selected = selected.copy()
    selected["_entry_ref_price"] = selected["close"].astype(float)
    selected["_exit_ref_price"] = selected["close"].astype(float) * (1.0 + selected[label_col].astype(float))
    keep = ["symbol", "timestamp", "open", "high", "low", "close", label_col, "_entry_ref_price", "_exit_ref_price"]
    out = selected[keep].dropna(subset=[label_col]).copy()
    out["entry_ts"] = out["timestamp"]
    out["exit_ts"] = out["timestamp"] + pd.to_timedelta(_tf_minutes(spec["timeframe"]) * int(spec["horizon"]), unit="m")
    out["entry_ref_price"] = out["_entry_ref_price"]
    out["exit_ref_price"] = out["_exit_ref_price"]
    out["source_return"] = out[label_col].astype(float)
    out["gross_source_return"] = out["source_return"]
    return out.drop(columns=["_entry_ref_price", "_exit_ref_price"])


def _metric_row(trades: pd.DataFrame, spec: dict[str, Any], cost: float) -> dict[str, Any]:
    base_id = _base_id(spec)
    candidate_id = f"{base_id}_c{str(cost).replace('.', 'p')}"
    if trades.empty:
        portfolio_returns = np.array([], dtype=float)
    else:
        net = trades[["timestamp", "source_return"]].copy()
        net["net"] = net["source_return"].astype(float) - 2.0 * float(cost) / 10000.0
        portfolio_returns = net.groupby("timestamp", sort=False)["net"].mean().to_numpy(dtype=float)
    return {
        "candidate_id": candidate_id,
        "base_candidate_id": base_id,
        "family": "exhaustive_intraday_long",
        "formula_id": int(spec["spec_id"]),
        "top_n": int(spec["max_positions"]),
        "horizon": int(spec["horizon"]),
        "cost_bps_per_side": float(cost),
        "spec": json.dumps(spec, sort_keys=True),
        **_metrics(portfolio_returns, trades["timestamp"] if not trades.empty else None, raw_trades=len(trades)),
    }


def _metrics(returns: np.ndarray, timestamps: pd.Series | None, raw_trades: int) -> dict[str, Any]:
    if len(returns) == 0:
        return {"trades": 0.0, "decision_points": 0.0, "win_rate": 0.0, "avg_return": 0.0, "log_total_return": 0.0, "total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0}
    clipped = np.clip(returns, -0.999999, None)
    log_rets = np.log1p(clipped)
    equity = np.exp(np.cumsum(log_rets))
    peak = np.maximum.accumulate(equity)
    dd = equity / np.maximum(peak, 1e-12) - 1.0
    days = 1
    if timestamps is not None and len(timestamps) > 1:
        days = max((pd.to_datetime(timestamps.max()) - pd.to_datetime(timestamps.min())).days, 1)
    log_total = float(log_rets.sum())
    return {
        "trades": float(raw_trades),
        "decision_points": float(len(returns)),
        "win_rate": float((returns > 0).mean()),
        "avg_return": float(returns.mean()),
        "log_total_return": log_total,
        "total_return": float(np.exp(log_total) - 1.0) if log_total < 700 else float("inf"),
        "cagr": float(np.exp(log_total * 365.25 / days) - 1.0) if log_total < 700 else float("inf"),
        "max_drawdown": float(dd.min()),
    }


def _select_trade_bases(leaderboard: pd.DataFrame, cost_summary: pd.DataFrame, keep: int) -> set[str]:
    buckets = []
    for cost in [0.0, 2.0, 5.0, 10.0]:
        sub = leaderboard[(leaderboard["cost_bps_per_side"] == cost) & (leaderboard["trades"] >= 50)]
        buckets.append(sub.sort_values(["cagr", "log_total_return"], ascending=False).drop_duplicates("base_candidate_id").head(max(20, keep // 5)))
        buckets.append(sub[(sub["cagr"] >= 0.20) & (sub["max_drawdown"] >= -0.50)].drop_duplicates("base_candidate_id").head(max(20, keep // 5)))
    survivors = cost_summary[(cost_summary["max_profitable_cost_bps_per_side"] >= 2.0) & (cost_summary["trades"] >= 50)]
    if not survivors.empty:
        ids = survivors.sort_values(["max_profitable_cost_bps_per_side", "best_cagr"], ascending=False).head(keep)["base_candidate_id"]
        buckets.append(leaderboard[leaderboard["base_candidate_id"].isin(ids)].drop_duplicates("base_candidate_id"))
    selected = pd.concat([b for b in buckets if not b.empty], ignore_index=True).drop_duplicates("base_candidate_id")
    return set(selected.head(keep)["base_candidate_id"].astype(str))


def _rebuild_selected_trades(
    config: dict[str, Any],
    specs: list[dict[str, Any]],
    selected_bases: set[str],
    log,
) -> pd.DataFrame:
    selected_specs = [spec for spec in specs if _base_id(spec) in selected_bases]
    if not selected_specs:
        return pd.DataFrame()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for spec in selected_specs:
        grouped.setdefault(spec["timeframe"], []).append(spec)
    parts: list[pd.DataFrame] = []
    for timeframe, group_specs in sorted(grouped.items()):
        horizons = sorted({int(s["horizon"]) for s in group_specs})
        rank_features = sorted({str(s["feature"]) for s in group_specs if str(s["selector"]).startswith("rank_")})
        log(f"rebuild_trades timeframe={timeframe} specs={len(group_specs)}")
        df = _load_timeframe_frame(config, timeframe, horizons)
        df = _prepare_frame(df, horizons, rank_features)
        for spec in group_specs:
            selected = _select(df, spec, f"fwd_return_{int(spec['horizon'])}")
            if selected.empty:
                continue
            base_id = _base_id(spec)
            selected = selected.copy()
            selected["candidate_id"] = base_id
            selected["rank_formula_id"] = int(spec["spec_id"])
            selected["top_n"] = int(spec.get("max_positions", 1))
            selected["discovery_cost_bps_per_side"] = 0.0
            parts.append(selected)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _base_id(spec: dict[str, Any]) -> str:
    return f"x{int(spec['spec_id']):07d}"


def _checkpoint_valid(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        cols = set(pd.read_csv(path, nrows=0).columns)
    except Exception:
        return False
    return {"candidate_id", "base_candidate_id", "cagr", "max_drawdown", "cost_bps_per_side"}.issubset(cols)


def _progress(kind: str, group_idx: int, groups: int, done: int, total: int, t0: float, timeframe: str, horizons: list[int]) -> str:
    elapsed = max(time.perf_counter() - t0, 1e-9)
    rate = done / elapsed
    eta = (total - done) / rate if rate > 0 else 0.0
    return f"{kind} group={group_idx}/{groups} timeframe={timeframe} horizons={horizons} done={done}/{total} rate_specs_sec={rate:.2f} eta_min={eta / 60:.1f}"


def _empty(label_col: str) -> pd.DataFrame:
    return pd.DataFrame(columns=["symbol", "timestamp", "open", "high", "low", "close", label_col, "entry_ts", "exit_ts", "entry_ref_price", "exit_ref_price", "source_return", "gross_source_return"])


def _tf_minutes(timeframe: str) -> int:
    if timeframe.endswith("m"):
        return int(timeframe[:-1])
    if timeframe.endswith("h"):
        return int(timeframe[:-1]) * 60
    if timeframe == "1d":
        return 390
    raise ValueError(timeframe)
