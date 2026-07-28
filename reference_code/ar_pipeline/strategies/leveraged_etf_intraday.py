from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import torch

from ar_pipeline.data import connect_catalog
from ar_pipeline.engines.cuda_discovery import summarize_cost_sensitivity, write_report
from ar_pipeline.execution import WorkloadInfo


TRADE_ASSETS = ["TQQQ", "SQQQ", "SOXL", "SOXS"]
SIGNAL_ASSETS = ["QQQ", "SMH", "TQQQ", "SQQQ", "SOXL", "SOXS", "NVDA", "AMD", "AVGO"]
CHIP_LEADERS = ["NVDA", "AMD", "AVGO"]
ALL_IDEAS = [
    "opening_range_breakout_breakdown",
    "vwap_trend_pullback",
    "opening_flush_reclaim_reversal",
    "intraday_momentum_into_close",
    "extreme_move_mean_reversion",
    "vwap_reclaim_scalp",
    "ema_trend_scalp",
    "opening_range_micro_scalp",
    "liquidity_sweep_reversal",
    "three_bar_momentum_scalp",
    "vwap_rejection_scalp",
    "gap_fill_strategy",
    "red_green_move",
    "ten_am_reversal",
    "power_hour_continuation",
    "leader_lagger_semiconductor_scalp",
    "failed_breakout_breakdown",
    "vwap_magnet_chop_scalp",
    "prior_session_high_low_break",
    "relative_strength_rotation_trade",
]


def estimate_workload(config: dict[str, Any]) -> WorkloadInfo:
    specs = _build_specs(config.get("scan", {}))
    return WorkloadInfo(
        pattern="leveraged_etf_intraday",
        preferred_device="cuda",
        supports_cuda=True,
        supports_cpu=True,
        supports_batch_autotune=False,
        estimated_rows=None,
        estimated_candidates=len(specs) * len(config.get("scan", {}).get("cost_bps_per_side_grid", [0.0])),
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
    batch_size = int(scan.get("batch_size", 2048))
    keep_trades_for = int(scan.get("keep_trades_for_top", 500))
    resume = bool(scan.get("resume", True))
    device = torch.device("cuda" if str(scan.get("device", "cuda")).lower() == "cuda" and torch.cuda.is_available() else "cpu")
    if str(scan.get("execution", {}).get("require_accelerated", False)).lower() in {"true", "1"} and device.type != "cuda":
        raise RuntimeError("leveraged_etf_intraday requires CUDA for this run")
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()

    def log(message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with progress_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    data = _load_scan_frame(config)
    feature_names = _feature_names()
    feature_np = data[feature_names].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=np.float32)
    ret_cols = [f"ret_{h}" for h in [1, 2, 3, 4, 6, 12, 24, 48]] + ["ret_close"]
    mae_cols = [f"mae_{h}" for h in [1, 2, 3, 4, 6, 12, 24, 48]]
    mfe_cols = [f"mfe_{h}" for h in [1, 2, 3, 4, 6, 12, 24, 48]]
    returns_np = data[ret_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=np.float32)
    mae_np = data[mae_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=np.float32)
    mfe_np = data[mfe_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=np.float32)
    trade_code_np = data["_trade_code"].to_numpy(dtype=np.int64)
    session_np = data["_session_ord"].to_numpy(dtype=np.int64)
    timestamp_np = pd.to_datetime(data["timestamp"], utc=True).to_numpy()
    timestamp_ns = timestamp_np.astype("datetime64[ns]").astype(np.int64, copy=False)

    features = torch.as_tensor(feature_np, device=device)
    returns = torch.as_tensor(returns_np, device=device)
    maes = torch.as_tensor(mae_np, device=device)
    mfes = torch.as_tensor(mfe_np, device=device)
    trade_codes = torch.as_tensor(trade_code_np, device=device)
    session_codes = torch.as_tensor(session_np, device=device)

    spec_arrays = _compile_specs(specs, feature_names)
    rows: list[pd.DataFrame] = []
    total_batches = math.ceil(len(specs) / batch_size)
    eval_t0 = time.perf_counter()
    log(f"start leveraged_etf_intraday rows={len(data)} specs={len(specs)} batches={total_batches} device={device} cost_grid={costs}")
    for batch_idx, start in enumerate(range(0, len(specs), batch_size), start=1):
        stop = min(start + batch_size, len(specs))
        batch_path = checkpoint_dir / f"batch_{batch_idx:05d}_{start}_{stop}.csv"
        if resume and _checkpoint_valid(batch_path):
            rows.append(pd.read_csv(batch_path))
            log(_progress("resume", batch_idx, total_batches, stop, len(specs), eval_t0, device))
            continue
        result = _evaluate_batch(
            spec_arrays,
            start,
            stop,
            features,
            returns,
            maes,
            mfes,
            trade_codes,
            session_codes,
            timestamp_ns,
            costs,
        )
        batch_df = pd.DataFrame(result)
        tmp = batch_path.with_suffix(".csv.tmp")
        batch_df.to_csv(tmp, index=False)
        tmp.replace(batch_path)
        rows.append(batch_df)
        log(_progress("batch", batch_idx, total_batches, stop, len(specs), eval_t0, device))

    leaderboard = pd.concat(rows, ignore_index=True)
    leaderboard = leaderboard.sort_values(["cagr", "log_total_return"], ascending=False).reset_index(drop=True)
    leaderboard.to_csv(output_dir / "leaderboard.csv", index=False)
    leaderboard.to_parquet(output_dir / "leaderboard.parquet", index=False)
    cost_summary = summarize_cost_sensitivity(leaderboard)
    cost_summary.to_csv(output_dir / "cost_sensitivity.csv", index=False)
    selected_bases = _select_trade_bases(leaderboard, cost_summary, keep_trades_for)
    trades = _rebuild_trades(data, specs, selected_bases, feature_names)
    trades.to_parquet(output_dir / "discovery_trades.parquet", index=False)
    (output_dir / "strategy_specs.json").write_text(json.dumps(specs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metadata = {
        "rows": int(len(data)),
        "specs": int(len(specs)),
        "formulas": int(len(specs)),
        "candidate_rows": int(len(leaderboard)),
        "cost_bps_per_side_grid": costs,
        "device": str(device),
        "cuda_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
        "cuda_memory_peak_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 3) if device.type == "cuda" else 0.0,
        "eval_elapsed_seconds": round(time.perf_counter() - eval_t0, 3),
        "total_elapsed_seconds": round(time.perf_counter() - t0, 3),
        "checkpoint_dir": str(checkpoint_dir),
        "progress_log": str(progress_path),
        "config": config,
    }
    metadata["formulas_per_second"] = round(len(specs) / max(float(metadata["eval_elapsed_seconds"]), 1e-9), 3)
    metadata["candidate_rows_per_second"] = round(len(leaderboard) / max(float(metadata["eval_elapsed_seconds"]), 1e-9), 3)
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(output_dir, leaderboard, metadata)
    log(f"done leveraged_etf_intraday candidate_rows={len(leaderboard)} elapsed_sec={metadata['total_elapsed_seconds']}")
    return {
        "leaderboard": str(output_dir / "leaderboard.csv"),
        "leaderboard_parquet": str(output_dir / "leaderboard.parquet"),
        "cost_sensitivity": str(output_dir / "cost_sensitivity.csv"),
        "trades": str(output_dir / "discovery_trades.parquet"),
        "report": str(output_dir / "discovery_report.md"),
        "strategy_specs": str(output_dir / "strategy_specs.json"),
    }


def _load_scan_frame(config: dict[str, Any]) -> pd.DataFrame:
    data_cfg = config.get("data", {})
    scan = config.get("scan", {})
    start = scan.get("train_start", "2019-06-21")
    end = scan.get("train_end", "2025-12-31")
    symbols = sorted(set(TRADE_ASSETS + SIGNAL_ASSETS))
    columns = [
        "symbol", "timestamp", "timeframe", "open", "high", "low", "close", "volume", "vwap",
        "ema_5", "ema_10", "ema_20", "close_vs_ema_20", "rsi_14", "relative_volume_20",
        "fwd_return_1", "fwd_return_2", "fwd_return_3", "fwd_return_4", "fwd_return_6",
        "fwd_return_12", "fwd_return_24", "fwd_return_48",
        "fwd_mfe_1", "fwd_mfe_2", "fwd_mfe_3", "fwd_mfe_4", "fwd_mfe_6",
        "fwd_mfe_12", "fwd_mfe_24", "fwd_mfe_48",
        "fwd_mae_1", "fwd_mae_2", "fwd_mae_3", "fwd_mae_4", "fwd_mae_6",
        "fwd_mae_12", "fwd_mae_24", "fwd_mae_48",
    ]
    con = connect_catalog(data_cfg.get("catalog_path"))
    try:
        available = {r[0] for r in con.execute("describe research_matrix").fetchall()}
        select_cols = [c for c in columns if c in available]
        df = con.execute(
            f"""
            select {", ".join(select_cols)}
            from research_matrix
            where timeframe = '5m'
              and symbol in ({",".join(["?"] * len(symbols))})
              and cast(timestamp as timestamp) >= ?
              and cast(timestamp as timestamp) <= ?
            order by timestamp, symbol
            """,
            symbols + [start, end],
        ).fetchdf()
    finally:
        con.close()
    if df.empty:
        raise ValueError("leveraged ETF scan query returned no rows")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="mixed")
    local = df["timestamp"].dt.tz_convert("America/New_York")
    df["_date"] = local.dt.strftime("%Y-%m-%d")
    df["_minute"] = (local.dt.hour * 60 + local.dt.minute) - (9 * 60 + 30)
    df = df[(df["_minute"] >= 0) & (df["_minute"] <= 385)].copy()
    df["_session"] = df["symbol"] + "|" + df["_date"]
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    df["_session_open"] = df.groupby("_session")["open"].transform("first").replace(0, np.nan)
    df["_session_return"] = df["close"].astype(float) / df["_session_open"].astype(float) - 1.0
    df["_prev_close"] = df.groupby("symbol")["close"].transform(lambda s: s.shift(1))
    first_mask = df.groupby("_session").cumcount().eq(0)
    prev_session_close = df.groupby("symbol")["close"].transform(lambda s: s.shift(1))
    df["_gap_return"] = np.where(first_mask, df["open"].astype(float) / prev_session_close.replace(0, np.nan).astype(float) - 1.0, np.nan)
    df["_gap_return"] = df.groupby("_session")["_gap_return"].transform("first").fillna(0.0)
    df["_close_vs_vwap"] = df["close"].astype(float) / df["vwap"].replace(0, np.nan).astype(float) - 1.0
    df["_dist_ema9"] = df["close"].astype(float) / df.get("ema_10", df["close"]).replace(0, np.nan).astype(float) - 1.0
    df["_dist_ema20"] = df["close"].astype(float) / df.get("ema_20", df["close"]).replace(0, np.nan).astype(float) - 1.0
    df["_bar_return"] = df.groupby("_session")["close"].pct_change().fillna(0.0)
    df["_prev_bar_return"] = df.groupby("_session")["_bar_return"].shift(1).fillna(0.0)
    df["_prev2_bar_return"] = df.groupby("_session")["_bar_return"].shift(2).fillna(0.0)
    df["_prev_close_vs_vwap"] = df.groupby("_session")["_close_vs_vwap"].shift(1).fillna(0.0)
    df["_prev_dist_ema9"] = df.groupby("_session")["_dist_ema9"].shift(1).fillna(0.0)
    df["_prev_dist_ema20"] = df.groupby("_session")["_dist_ema20"].shift(1).fillna(0.0)
    daily = df.groupby(["symbol", "_date"]).agg(day_high=("high", "max"), day_low=("low", "min"), day_close=("close", "last")).reset_index()
    daily[["_prev_day_high", "_prev_day_low", "_prev_day_close"]] = daily.groupby("symbol")[["day_high", "day_low", "day_close"]].shift(1)
    df = df.merge(daily[["symbol", "_date", "_prev_day_high", "_prev_day_low", "_prev_day_close"]], on=["symbol", "_date"], how="left")
    df[["_prev_day_high", "_prev_day_low", "_prev_day_close"]] = df[["_prev_day_high", "_prev_day_low", "_prev_day_close"]].ffill().bfill()
    df["_prev_day_return"] = df["close"].astype(float) / df["_prev_day_close"].replace(0, np.nan).astype(float) - 1.0
    early = df[df["_minute"] < 330].copy()
    early_hi = early.groupby("_session")["high"].transform("max")
    early_lo = early.groupby("_session")["low"].transform("min")
    df["_pre_power_high"] = early_hi.reindex(df.index)
    df["_pre_power_low"] = early_lo.reindex(df.index)
    df[["_pre_power_high", "_pre_power_low"]] = df.groupby("_session")[["_pre_power_high", "_pre_power_low"]].transform("max").ffill().bfill()
    for rng in [5, 10, 15, 30]:
        hi = df[df["_minute"] <= rng].groupby("_session")["high"].transform("max")
        lo = df[df["_minute"] <= rng].groupby("_session")["low"].transform("min")
        mid = (hi + lo) / 2.0
        df[f"_or_high_{rng}"] = hi.reindex(df.index)
        df[f"_or_low_{rng}"] = lo.reindex(df.index)
        df[f"_or_mid_{rng}"] = mid.reindex(df.index)
        df[[f"_or_high_{rng}", f"_or_low_{rng}", f"_or_mid_{rng}"]] = df.groupby("_session")[[f"_or_high_{rng}", f"_or_low_{rng}", f"_or_mid_{rng}"]].transform("first")
    df["_roll_high_3"] = df.groupby("_session")["high"].rolling(3, min_periods=1).max().reset_index(level=0, drop=True)
    df["_roll_low_3"] = df.groupby("_session")["low"].rolling(3, min_periods=1).min().reset_index(level=0, drop=True)
    df["_session_volume_cum"] = df.groupby("_session")["volume"].cumsum()
    df["_vol_fading"] = (df["relative_volume_20"].fillna(1.0) < 1.0).astype(float)
    wide_cols = [
        "open", "high", "low", "close", "vwap", "_session_return", "_gap_return", "_close_vs_vwap",
        "_dist_ema9", "_dist_ema20", "rsi_14", "relative_volume_20", "_vol_fading", "_roll_high_3", "_roll_low_3",
        "_bar_return", "_prev_bar_return", "_prev2_bar_return", "_prev_close_vs_vwap", "_prev_dist_ema9", "_prev_dist_ema20",
        "_prev_day_high", "_prev_day_low", "_prev_day_close", "_prev_day_return", "_pre_power_high", "_pre_power_low",
    ] + [f"_or_high_{x}" for x in [5, 10, 15, 30]] + [f"_or_low_{x}" for x in [5, 10, 15, 30]] + [f"_or_mid_{x}" for x in [5, 10, 15, 30]]
    wide = df.pivot(index="timestamp", columns="symbol", values=wide_cols)
    wide.columns = [f"{sym}__{col}" for col, sym in wide.columns]
    rows = df[df["symbol"].isin(TRADE_ASSETS)].copy()
    rows = rows.merge(wide.reset_index(), on="timestamp", how="left")
    for h in [1, 2, 3, 4, 6, 12, 24, 48]:
        rows[f"ret_{h}"] = rows[f"fwd_return_{h}"]
        rows[f"mae_{h}"] = rows[f"fwd_mae_{h}"]
        rows[f"mfe_{h}"] = rows[f"fwd_mfe_{h}"]
    eod_close = rows.groupby(["symbol", "_date"])["close"].transform("last")
    rows["_session_close_ts"] = rows.groupby(["symbol", "_date"])["timestamp"].transform("last")
    rows["ret_close"] = eod_close.astype(float) / rows["close"].replace(0, np.nan).astype(float) - 1.0
    rows["_trade_code"] = rows["symbol"].map({s: i for i, s in enumerate(TRADE_ASSETS)}).astype(int)
    rows["_session_ord"] = pd.factorize(rows["symbol"] + "|" + rows["_date"])[0]
    return rows.reset_index(drop=True)


def _feature_names() -> list[str]:
    names = ["_minute"]
    for sym in SIGNAL_ASSETS:
        for col in [
            "close", "high", "low", "vwap", "_session_return", "_gap_return", "_close_vs_vwap",
            "_dist_ema9", "_dist_ema20", "rsi_14", "relative_volume_20", "_vol_fading", "_roll_high_3", "_roll_low_3",
            "_bar_return", "_prev_bar_return", "_prev2_bar_return", "_prev_close_vs_vwap", "_prev_dist_ema9", "_prev_dist_ema20",
            "_prev_day_high", "_prev_day_low", "_prev_day_close", "_prev_day_return", "_pre_power_high", "_pre_power_low",
        ]:
            names.append(f"{sym}__{col}")
        for rng in [5, 10, 15, 30]:
            names.extend([f"{sym}___or_high_{rng}", f"{sym}___or_low_{rng}", f"{sym}___or_mid_{rng}"])
    return names


def _build_specs(scan: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    trade_assets = scan.get("trade_assets", TRADE_ASSETS)
    signal_assets = scan.get("signal_assets", ["QQQ", "SMH", "TQQQ", "SQQQ", "SOXL", "SOXS", "NVDA", "AMD", "AVGO"])
    confirmations = scan.get("confirmations", ["none", "leaders_2of3_vwap", "leaders_2of3_return"])
    ideas = set(scan.get("ideas", ALL_IDEAS))
    costs = scan.get("cost_bps_per_side_grid", [0, 2, 5, 10, 25, 50])
    _ = costs

    def add(family: str, **kw: Any) -> None:
        spec = {"spec_id": len(specs), "family": family, **kw}
        specs.append(spec)

    def enabled(name: str) -> bool:
        return name in ideas

    # 1. Opening range breakout / breakdown.
    if enabled("opening_range_breakout_breakdown"):
        for trade in trade_assets:
            for sig in signal_assets:
                for side in ["bull", "bear"]:
                    for confirmation in _confirmations_for(trade, sig, confirmations):
                        for confirm_threshold in _confirm_thresholds(confirmation):
                            for rng in [5, 10, 15]:
                                for start_after in [rng, rng + 10, 60]:
                                    for end_min in [90, 180, 385]:
                                        if start_after >= end_min:
                                            continue
                                        for buffer_bps in [0, 5, 15]:
                                            for vwap_filter in ["signal", "trade", "both"]:
                                                for horizon in [3, 6, 12, 24, "close"]:
                                                    for stop in ["none", "range_mid", "vwap", "wide_150bps"]:
                                                        add("opening_range_breakout_breakdown", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, range_minutes=rng, start_minute=start_after, end_minute=end_min, buffer_bps=buffer_bps, vwap_filter=vwap_filter, horizon=horizon, stop_model=stop)

    # 2. VWAP trend-continuation pullback.
    if enabled("vwap_trend_pullback"):
      for trade in trade_assets:
        for sig in signal_assets:
            for side in ["bull", "bear"]:
                for confirmation in _confirmations_for(trade, sig, confirmations):
                    for confirm_threshold in _confirm_thresholds(confirmation):
                        for trend_minute in [15, 30, 60]:
                            for entry_start, entry_end in [(20, 120), (30, 180), (60, 240), (120, 330)]:
                                for pull_ref in ["vwap", "ema9", "ema20"]:
                                    for tolerance_bps in [10, 25, 50]:
                                        for trend_ret in [0.0025, 0.005, 0.01, 0.02]:
                                            for pull_number in [1, 2]:
                                                for horizon in [2, 3, 6, 12, "close"]:
                                                    add("vwap_trend_pullback", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, trend_minute=trend_minute, start_minute=entry_start, end_minute=entry_end, pull_ref=pull_ref, tolerance_bps=tolerance_bps, trend_return=trend_ret, pull_number=pull_number, horizon=horizon, stop_model="vwap_or_ema")

    # 3. Opening flush/reclaim reversal.
    if enabled("opening_flush_reclaim_reversal"):
      for trade in trade_assets:
        for sig in signal_assets:
            for side in ["bull_reclaim", "bear_fail"]:
                for confirmation in _confirmations_for(trade, sig, confirmations):
                    for confirm_threshold in _confirm_thresholds(confirmation):
                        for rng in [5, 10, 15]:
                            for shock in [0.005, 0.01, 0.02, 0.04]:
                                for reclaim in ["vwap", "or_mid", "or_high_low"]:
                                    for start_min, end_min in [(10, 90), (15, 120), (30, 180), (60, 240)]:
                                        for horizon in [2, 3, 6, 12, "close"]:
                                            add("opening_flush_reclaim_reversal", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, range_minutes=rng, shock_return=shock, reclaim=reclaim, start_minute=start_min, end_minute=end_min, horizon=horizon, stop_model="lod_hod_or_vwap")

    # 4. Intraday momentum into the close.
    if enabled("intraday_momentum_into_close"):
      for trade in trade_assets:
        for sig in signal_assets:
            for side in ["bull", "bear"]:
                for confirmation in _confirmations_for(trade, sig, confirmations):
                    for confirm_threshold in _confirm_thresholds(confirmation):
                        for measure_min in [30, 60, 120]:
                            for threshold in [0.0025, 0.005, 0.01, 0.025, 0.04]:
                                for entry_start, entry_end in [(180, 330), (240, 360), (300, 385)]:
                                    for hold in [3, 6, 12, "close"]:
                                        for require_pullback in [False, True]:
                                            add("intraday_momentum_into_close", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, measure_minute=measure_min, threshold=threshold, start_minute=entry_start, end_minute=entry_end, horizon=hold, require_pullback=require_pullback, stop_model="afternoon_vwap")

    # 5. Extreme move mean reversion.
    if enabled("extreme_move_mean_reversion"):
      for trade in trade_assets:
        for sig in signal_assets:
            for fade_side in ["fade_up", "fade_down"]:
                for confirmation in _confirmations_for(trade, sig, confirmations):
                    for confirm_threshold in _confirm_thresholds(confirmation):
                        for entry_start, entry_end in [(30, 120), (60, 180), (90, 240), (180, 360)]:
                            for ext_bps in [50, 100, 200, 400]:
                                for rsi_level in [25, 30, 70, 75]:
                                    for vol_req in ["none", "fading"]:
                                        for fail in ["roll3", "vwap_stall"]:
                                            for horizon in [1, 2, 3, 6, 12]:
                                                add("extreme_move_mean_reversion", trade_asset=trade, signal_asset=sig, side=fade_side, confirmation=confirmation, confirm_threshold=confirm_threshold, start_minute=entry_start, end_minute=entry_end, extension_bps=ext_bps, rsi_level=rsi_level, volume_requirement=vol_req, failure_test=fail, horizon=horizon, stop_model="hard_stop")

    # 6-15. Tactical ETF day-trading ideas. These are strategic variants, not just parameter clones.
    for trade in trade_assets:
        for sig in signal_assets:
            for side in ["bull", "bear"]:
                for confirmation in _confirmations_for(trade, sig, confirmations):
                    for confirm_threshold in _confirm_thresholds(confirmation):
                        for horizon in [1, 2, 3, 6, 12, "close"]:
                            if enabled("vwap_reclaim_scalp"):
                                for start_min, end_min in [(15, 90), (15, 120), (30, 180)]:
                                    for reclaim_bps in [0, 5, 15, 30]:
                                        add("vwap_reclaim_scalp", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, start_minute=start_min, end_minute=end_min, reclaim_bps=reclaim_bps, horizon=horizon, stop_model="tight_50bps")
                            if enabled("ema_trend_scalp"):
                                for start_min, end_min in [(15, 120), (30, 180), (60, 240)]:
                                    for ema_ref in ["ema9", "ema20", "stacked"]:
                                        for tolerance_bps in [10, 25, 50]:
                                            add("ema_trend_scalp", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, start_minute=start_min, end_minute=end_min, ema_ref=ema_ref, tolerance_bps=tolerance_bps, horizon=horizon, stop_model="vwap_or_ema")
                            if enabled("opening_range_micro_scalp"):
                                for rng in [5, 10]:
                                    for start_min, end_min in [(rng, 45), (rng, 90), (15, 120)]:
                                        for buffer_bps in [0, 5, 10]:
                                            add("opening_range_micro_scalp", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, range_minutes=rng, start_minute=start_min, end_minute=end_min, buffer_bps=buffer_bps, horizon=horizon, stop_model="tight_50bps")
                            if enabled("three_bar_momentum_scalp"):
                                for start_min, end_min in [(10, 90), (15, 120), (30, 180)]:
                                    for min_bar_return in [0.0, 0.001, 0.0025]:
                                        add("three_bar_momentum_scalp", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, start_minute=start_min, end_minute=end_min, min_bar_return=min_bar_return, horizon=horizon, stop_model="tight_50bps")
                            if enabled("vwap_rejection_scalp"):
                                for start_min, end_min in [(20, 120), (60, 240), (120, 330)]:
                                    for reject_bps in [10, 25, 50]:
                                        add("vwap_rejection_scalp", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, start_minute=start_min, end_minute=end_min, reject_bps=reject_bps, horizon=horizon, stop_model="vwap")
                            if enabled("failed_breakout_breakdown"):
                                for rng in [5, 10, 15, 30]:
                                    for start_min, end_min in [(rng, 120), (30, 180), (60, 240)]:
                                        add("failed_breakout_breakdown", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, range_minutes=rng, start_minute=start_min, end_minute=end_min, horizon=horizon, stop_model="range_mid")
                            if enabled("vwap_magnet_chop_scalp"):
                                for start_min, end_min in [(30, 180), (60, 240), (120, 330)]:
                                    for extension_bps in [50, 100, 150, 250]:
                                        add("vwap_magnet_chop_scalp", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, start_minute=start_min, end_minute=end_min, extension_bps=extension_bps, horizon=horizon, stop_model="hard_stop")
                            if enabled("prior_session_high_low_break"):
                                for start_min, end_min in [(0, 90), (15, 180), (60, 330)]:
                                    for buffer_bps in [0, 5, 15]:
                                        add("prior_session_high_low_break", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, start_minute=start_min, end_minute=end_min, buffer_bps=buffer_bps, horizon=horizon, stop_model="wide_150bps")

    for trade in trade_assets:
        for sig in signal_assets:
            for side in ["bull_reclaim", "bear_fail"]:
                for confirmation in _confirmations_for(trade, sig, confirmations):
                    for confirm_threshold in _confirm_thresholds(confirmation):
                        for horizon in [1, 2, 3, 6, 12, "close"]:
                            if enabled("liquidity_sweep_reversal"):
                                for rng in [5, 10, 15, 30]:
                                    for start_min, end_min in [(rng, 90), (30, 180), (60, 240)]:
                                        for sweep_bps in [0, 5, 15, 30]:
                                            add("liquidity_sweep_reversal", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, range_minutes=rng, start_minute=start_min, end_minute=end_min, sweep_bps=sweep_bps, horizon=horizon, stop_model="lod_hod_or_vwap")
                            if enabled("gap_fill_strategy"):
                                for start_min, end_min in [(5, 90), (15, 180), (30, 330)]:
                                    for gap_threshold in [0.0025, 0.005, 0.01, 0.02]:
                                        add("gap_fill_strategy", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, start_minute=start_min, end_minute=end_min, gap_threshold=gap_threshold, horizon=horizon, stop_model="vwap")
                            if enabled("red_green_move"):
                                for start_min, end_min in [(5, 120), (15, 180), (30, 240)]:
                                    for gap_threshold in [0.0, 0.0025, 0.005, 0.01]:
                                        add("red_green_move", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, start_minute=start_min, end_minute=end_min, gap_threshold=gap_threshold, horizon=horizon, stop_model="vwap")
                            if enabled("ten_am_reversal"):
                                for start_min, end_min in [(25, 60), (30, 90), (45, 120)]:
                                    for shock in [0.005, 0.01, 0.02, 0.04]:
                                        add("ten_am_reversal", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, start_minute=start_min, end_minute=end_min, shock_return=shock, horizon=horizon, stop_model="lod_hod_or_vwap")

    if enabled("power_hour_continuation"):
        for trade in trade_assets:
            for sig in signal_assets:
                for side in ["bull", "bear"]:
                    for confirmation in _confirmations_for(trade, sig, confirmations):
                        for confirm_threshold in _confirm_thresholds(confirmation):
                            for threshold in [0.0025, 0.005, 0.01, 0.02]:
                                for horizon in [1, 2, 3, 6, "close"]:
                                    add("power_hour_continuation", trade_asset=trade, signal_asset=sig, side=side, confirmation=confirmation, confirm_threshold=confirm_threshold, start_minute=330, end_minute=385, threshold=threshold, horizon=horizon, stop_model="afternoon_vwap")

    if enabled("leader_lagger_semiconductor_scalp"):
        for trade in trade_assets:
            for leader in CHIP_LEADERS + ["SMH"]:
                for side in ["bull", "bear"]:
                    for threshold in [0.0025, 0.005, 0.01, 0.02]:
                        for start_min, end_min in [(15, 120), (30, 180), (60, 240)]:
                            for horizon in [1, 2, 3, 6, 12]:
                                add("leader_lagger_semiconductor_scalp", trade_asset=trade, signal_asset=leader, side=side, confirmation="leaders_2of3_return", confirm_threshold=0.0, start_minute=start_min, end_minute=end_min, threshold=threshold, horizon=horizon, stop_model="tight_50bps")

    if enabled("relative_strength_rotation_trade"):
        for trade in trade_assets:
            for side in ["bull", "bear"]:
                for spread_threshold in [0.0025, 0.005, 0.01, 0.02]:
                    for start_min, end_min in [(15, 120), (30, 240), (120, 360)]:
                        for horizon in [2, 3, 6, 12, "close"]:
                            add("relative_strength_rotation_trade", trade_asset=trade, signal_asset="SMH", side=side, confirmation="leaders_2of3_return", confirm_threshold=0.0, start_minute=start_min, end_minute=end_min, spread_threshold=spread_threshold, horizon=horizon, stop_model="vwap")

    max_specs = int(scan.get("max_specs", 0) or 0)
    if max_specs > 0 and len(specs) > max_specs:
        specs = _budget_specs(specs, max_specs)
    for i, spec in enumerate(specs):
        spec["spec_id"] = i
    return specs


def _budget_specs(specs: list[dict[str, Any]], max_specs: int) -> list[dict[str, Any]]:
    by_family: dict[str, list[dict[str, Any]]] = {}
    for spec in specs:
        by_family.setdefault(str(spec["family"]), []).append(spec)
    total = len(specs)
    family_items = sorted(by_family.items(), key=lambda kv: kv[0])
    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, int]] = set()
    equal_budget = max_specs // 2
    equal_quota = max(1, equal_budget // max(len(family_items), 1))
    for family, group in family_items:
        for item in _even_sample(group, min(len(group), equal_quota)):
            key = (family, int(item["spec_id"]))
            if key not in selected_keys:
                selected.append(item)
                selected_keys.add(key)
    remaining = max_specs - len(selected)
    if remaining > 0:
        for family, group in family_items:
            quota = min(len(group), max(1, round(remaining * len(group) / max(total, 1))))
            for item in _even_sample(group, quota):
                key = (family, int(item["spec_id"]))
                if key not in selected_keys:
                    selected.append(item)
                    selected_keys.add(key)
                    if len(selected) >= max_specs:
                        break
            if len(selected) >= max_specs:
                break
    if len(selected) > max_specs:
        selected = _even_sample(selected, max_specs)
    return selected


def _even_sample(items: list[dict[str, Any]], quota: int) -> list[dict[str, Any]]:
    if quota >= len(items):
        return list(items)
    if quota <= 0:
        return []
    idxs = np.linspace(0, len(items) - 1, quota, dtype=np.int64)
    return [items[int(i)] for i in idxs]


def _confirmations_for(trade: str, signal: str, confirmations: list[str]) -> list[str]:
    if trade in {"SOXL", "SOXS"} or signal in {"SMH", "SOXL", "SOXS", *CHIP_LEADERS}:
        return list(confirmations)
    return ["none"]


def _confirm_thresholds(confirmation: str) -> list[float]:
    if confirmation == "none":
        return [0.0]
    return [0.0, 0.005]


def _compile_specs(specs: list[dict[str, Any]], feature_names: list[str]) -> dict[str, Any]:
    fmap = {name: i for i, name in enumerate(feature_names)}
    return {
        "specs": specs,
        "fmap": fmap,
        "trade_code": np.array([TRADE_ASSETS.index(s["trade_asset"]) for s in specs], dtype=np.int64),
        "horizon_idx": np.array([_horizon_index(s["horizon"]) for s in specs], dtype=np.int64),
        "stop_bps": np.array([_stop_bps(s.get("stop_model")) for s in specs], dtype=np.float32),
        "target_bps": np.array([_target_bps(s.get("family")) for s in specs], dtype=np.float32),
    }


def _evaluate_batch(compiled: dict[str, Any], start: int, stop: int, features: torch.Tensor, returns: torch.Tensor, maes: torch.Tensor, mfes: torch.Tensor, trade_codes: torch.Tensor, session_codes: torch.Tensor, timestamp_ns: np.ndarray, costs: list[float]) -> list[dict[str, Any]]:
    specs = compiled["specs"][start:stop]
    fmap = compiled["fmap"]
    with torch.inference_mode():
        masks = []
        for spec in specs:
            masks.append(_mask_for_spec(spec, features, fmap, trade_codes))
        mask = torch.stack(masks, dim=1)
        horizon_idx = torch.as_tensor(compiled["horizon_idx"][start:stop], device=features.device)
        selected_returns = returns[:, horizon_idx]
        selected_mae = maes[:, torch.clamp(horizon_idx, max=7)]
        selected_mfe = mfes[:, torch.clamp(horizon_idx, max=7)]
        stop_tensor = torch.as_tensor(compiled["stop_bps"][start:stop], device=features.device) / 10000.0
        target = torch.as_tensor(compiled["target_bps"][start:stop], device=features.device) / 10000.0
        gross = selected_returns
        stopped = (stop_tensor > 0).unsqueeze(0) & (selected_mae <= -stop_tensor.unsqueeze(0))
        targeted = (target > 0).unsqueeze(0) & (selected_mfe >= target.unsqueeze(0))
        gross = torch.where(stopped, -stop_tensor.unsqueeze(0), torch.where(targeted, target.unsqueeze(0), gross))
        gross = torch.where(mask, gross, torch.nan)
        valid = torch.isfinite(gross)
        trade_counts, first_idx, last_idx = _valid_position_stats(valid)
    rows: list[dict[str, Any]] = []
    for cost in costs:
        with torch.inference_mode():
            metrics_by_col = _metrics_gpu(gross, valid, trade_counts, first_idx, last_idx, timestamp_ns, float(cost))
        for local_i, spec in enumerate(specs):
            base = _base_id(spec)
            rows.append({
                "candidate_id": f"{base}_c{str(cost).replace('.', 'p')}",
                "base_candidate_id": base,
                "family": spec["family"],
                "formula_id": int(spec["spec_id"]),
                "top_n": 1,
                "horizon": str(spec["horizon"]),
                "cost_bps_per_side": float(cost),
                "spec": json.dumps(spec, sort_keys=True),
                **{k: v[local_i] for k, v in metrics_by_col.items()},
            })
    return rows


def _valid_position_stats(valid: torch.Tensor) -> tuple[torch.Tensor, np.ndarray, np.ndarray]:
    n = valid.shape[0]
    idx = torch.arange(n, device=valid.device).unsqueeze(1)
    counts = valid.sum(dim=0)
    first = torch.where(valid, idx, torch.full_like(idx, n)).min(dim=0).values
    last = torch.where(valid, idx, torch.zeros_like(idx)).max(dim=0).values
    return counts, first.detach().cpu().numpy(), last.detach().cpu().numpy()


def _metrics_gpu(gross: torch.Tensor, valid: torch.Tensor, counts: torch.Tensor, first_idx: np.ndarray, last_idx: np.ndarray, timestamp_ns: np.ndarray, cost_bps: float) -> dict[str, list[float]]:
    cost = 2.0 * cost_bps / 10000.0
    returns = torch.where(valid, gross - cost, torch.zeros_like(gross))
    safe_counts = counts.clamp_min(1)
    wins = ((returns > 0) & valid).sum(dim=0).float() / safe_counts.float()
    avg = returns.sum(dim=0) / safe_counts.float()
    log_vals = torch.where(valid, torch.log1p(torch.clamp(returns, min=-0.999999)), torch.zeros_like(returns))
    log_total = log_vals.sum(dim=0)
    equity = torch.exp(torch.cumsum(log_vals, dim=0))
    peak = torch.cummax(equity, dim=0).values.clamp_min(1e-12)
    drawdown = (equity / peak - 1.0).min(dim=0).values
    counts_np = counts.detach().cpu().numpy().astype(np.float64)
    log_np = log_total.detach().cpu().numpy().astype(np.float64)
    days = np.ones_like(log_np, dtype=np.float64)
    active = counts_np > 0
    if active.any():
        span_ns = timestamp_ns[last_idx[active]] - timestamp_ns[first_idx[active]]
        days[active] = np.maximum(span_ns / (24 * 60 * 60 * 1_000_000_000), 1.0)
    total_return = np.where(log_np < 700, np.exp(log_np) - 1.0, np.inf)
    cagr = np.where(log_np < 700, np.exp(log_np * 365.25 / days) - 1.0, np.inf)
    zero = counts_np <= 0
    if zero.any():
        total_return[zero] = 0.0
        cagr[zero] = 0.0
    return {
        "trades": counts_np.tolist(),
        "decision_points": counts_np.tolist(),
        "win_rate": wins.detach().cpu().numpy().astype(np.float64).tolist(),
        "avg_return": avg.detach().cpu().numpy().astype(np.float64).tolist(),
        "log_total_return": log_np.tolist(),
        "total_return": total_return.tolist(),
        "cagr": cagr.tolist(),
        "max_drawdown": drawdown.detach().cpu().numpy().astype(np.float64).tolist(),
    }


def _mask_for_spec(spec: dict[str, Any], features: torch.Tensor, fmap: dict[str, int], trade_codes: torch.Tensor) -> torch.Tensor:
    sig = spec["signal_asset"]
    trade = spec["trade_asset"]
    trade_code = TRADE_ASSETS.index(spec["trade_asset"])
    minute = features[:, fmap["_minute"]]
    close = features[:, fmap[f"{sig}__close"]]
    high = features[:, fmap[f"{sig}__high"]]
    low = features[:, fmap[f"{sig}__low"]]
    vwap_side = features[:, fmap[f"{sig}___close_vs_vwap"]]
    sess_ret = features[:, fmap[f"{sig}___session_return"]]
    gap = features[:, fmap[f"{sig}___gap_return"]]
    rsi = features[:, fmap[f"{sig}__rsi_14"]]
    relvol = features[:, fmap[f"{sig}__relative_volume_20"]]
    mask = trade_codes.eq(trade_code)
    mask &= minute.ge(float(spec.get("start_minute", 0))) & minute.le(float(spec.get("end_minute", 385)))
    side = str(spec.get("side", "bull"))
    bull = side in {"bull", "bull_reclaim", "fade_down"}
    mask &= _confirmation_filter(spec, features, fmap, bull)
    if spec["family"] == "opening_range_breakout_breakdown":
        rng = int(spec["range_minutes"])
        hi = features[:, fmap[f"{sig}___or_high_{rng}"]]
        lo = features[:, fmap[f"{sig}___or_low_{rng}"]]
        buf = float(spec["buffer_bps"]) / 10000.0
        break_ok = close.gt(hi * (1.0 + buf)) if bull else close.lt(lo * (1.0 - buf))
        mask &= break_ok
        mask &= _vwap_filter(spec, features, fmap, sig, trade, bull)
    elif spec["family"] == "vwap_trend_pullback":
        trend = float(spec["trend_return"])
        trend_ok = sess_ret.ge(trend) if bull else sess_ret.le(-trend)
        ref_col = {"vwap": "vwap", "ema9": "_dist_ema9", "ema20": "_dist_ema20"}[spec["pull_ref"]]
        if ref_col == "vwap":
            dist = vwap_side
        else:
            dist = features[:, fmap[f"{sig}__{ref_col}"]]
        tol = float(spec["tolerance_bps"]) / 10000.0
        mask &= trend_ok & dist.abs().le(tol) & _vwap_filter({"vwap_filter": "signal"}, features, fmap, sig, trade, bull)
        if int(spec["pull_number"]) == 1:
            mask &= minute.le(float(spec.get("start_minute", 0)) + 60)
        elif int(spec["pull_number"]) == 2:
            mask &= minute.gt(float(spec.get("start_minute", 0)) + 30)
    elif spec["family"] == "opening_flush_reclaim_reversal":
        rng = int(spec["range_minutes"])
        mid = features[:, fmap[f"{sig}___or_mid_{rng}"]]
        shock = float(spec["shock_return"])
        if side == "bull_reclaim":
            mask &= (gap.le(-shock) | sess_ret.le(-shock))
            if spec["reclaim"] == "vwap":
                mask &= vwap_side.gt(0)
            elif spec["reclaim"] == "or_mid":
                mask &= close.gt(mid)
            else:
                mask &= close.gt(features[:, fmap[f"{sig}___or_high_{rng}"]])
        else:
            mask &= (gap.ge(shock) | sess_ret.ge(shock))
            if spec["reclaim"] == "vwap":
                mask &= vwap_side.lt(0)
            elif spec["reclaim"] == "or_mid":
                mask &= close.lt(mid)
            else:
                mask &= close.lt(features[:, fmap[f"{sig}___or_low_{rng}"]])
    elif spec["family"] == "intraday_momentum_into_close":
        thresh = float(spec["threshold"])
        momentum_ok = sess_ret.ge(thresh) if bull else sess_ret.le(-thresh)
        mask &= momentum_ok
        mask &= _vwap_filter({"vwap_filter": "signal"}, features, fmap, sig, trade, bull)
        if bool(spec["require_pullback"]):
            mask &= vwap_side.abs().le(0.004)
    elif spec["family"] == "extreme_move_mean_reversion":
        ext = float(spec["extension_bps"]) / 10000.0
        if side == "fade_up":
            mask &= vwap_side.ge(ext) & rsi.ge(float(spec["rsi_level"]))
        else:
            mask &= vwap_side.le(-ext) & rsi.le(float(spec["rsi_level"]))
        if spec["volume_requirement"] == "fading":
            mask &= relvol.lt(1.0)
        if spec["failure_test"] == "roll3":
            roll = features[:, fmap[f"{sig}___roll_high_3"]] if side == "fade_up" else features[:, fmap[f"{sig}___roll_low_3"]]
            failure_ok = close.lt(roll) if side == "fade_up" else close.gt(roll)
            mask &= failure_ok
    elif spec["family"] == "vwap_reclaim_scalp":
        reclaim = float(spec.get("reclaim_bps", 0.0)) / 10000.0
        prev_vwap = features[:, fmap[f"{sig}___prev_close_vs_vwap"]]
        trade_vwap = features[:, fmap[f"{trade}___close_vs_vwap"]]
        if bull:
            mask &= prev_vwap.le(-reclaim) & vwap_side.gt(0) & trade_vwap.gt(0)
        else:
            mask &= prev_vwap.ge(reclaim) & vwap_side.lt(0) & trade_vwap.lt(0)
    elif spec["family"] == "ema_trend_scalp":
        tol = float(spec.get("tolerance_bps", 25.0)) / 10000.0
        dist9 = features[:, fmap[f"{sig}___dist_ema9"]]
        dist20 = features[:, fmap[f"{sig}___dist_ema20"]]
        prev9 = features[:, fmap[f"{sig}___prev_dist_ema9"]]
        ref = str(spec.get("ema_ref", "ema9"))
        if bull:
            trend_ok = dist20.gt(0) if ref != "stacked" else (dist9.gt(0) & dist20.gt(0))
            pull_ok = prev9.le(tol) & dist9.gt(0)
            mask &= trend_ok & pull_ok & vwap_side.gt(0)
        else:
            trend_ok = dist20.lt(0) if ref != "stacked" else (dist9.lt(0) & dist20.lt(0))
            pull_ok = prev9.ge(-tol) & dist9.lt(0)
            mask &= trend_ok & pull_ok & vwap_side.lt(0)
    elif spec["family"] == "opening_range_micro_scalp":
        rng = int(spec["range_minutes"])
        hi = features[:, fmap[f"{sig}___or_high_{rng}"]]
        lo = features[:, fmap[f"{sig}___or_low_{rng}"]]
        buf = float(spec.get("buffer_bps", 0.0)) / 10000.0
        if bull:
            mask &= close.gt(hi * (1.0 + buf)) & vwap_side.gt(0)
        else:
            mask &= close.lt(lo * (1.0 - buf)) & vwap_side.lt(0)
    elif spec["family"] == "liquidity_sweep_reversal":
        rng = int(spec["range_minutes"])
        sweep = float(spec.get("sweep_bps", 0.0)) / 10000.0
        hi = features[:, fmap[f"{sig}___or_high_{rng}"]]
        lo = features[:, fmap[f"{sig}___or_low_{rng}"]]
        if side == "bull_reclaim":
            mask &= low.lt(lo * (1.0 - sweep)) & close.gt(lo) & vwap_side.gt(-0.0025)
        else:
            mask &= high.gt(hi * (1.0 + sweep)) & close.lt(hi) & vwap_side.lt(0.0025)
    elif spec["family"] == "three_bar_momentum_scalp":
        br0 = features[:, fmap[f"{sig}___bar_return"]]
        br1 = features[:, fmap[f"{sig}___prev_bar_return"]]
        br2 = features[:, fmap[f"{sig}___prev2_bar_return"]]
        min_ret = float(spec.get("min_bar_return", 0.0))
        if bull:
            mask &= br0.ge(min_ret) & br1.ge(min_ret) & br2.ge(min_ret) & vwap_side.gt(0)
        else:
            mask &= br0.le(-min_ret) & br1.le(-min_ret) & br2.le(-min_ret) & vwap_side.lt(0)
    elif spec["family"] == "vwap_rejection_scalp":
        reject = float(spec.get("reject_bps", 25.0)) / 10000.0
        prev_vwap = features[:, fmap[f"{sig}___prev_close_vs_vwap"]]
        if bull:
            mask &= prev_vwap.ge(0) & vwap_side.ge(0) & vwap_side.le(reject)
        else:
            mask &= prev_vwap.le(0) & vwap_side.le(0) & vwap_side.ge(-reject)
    elif spec["family"] == "gap_fill_strategy":
        gap_threshold = float(spec.get("gap_threshold", 0.005))
        prev_close = features[:, fmap[f"{sig}___prev_day_close"]]
        if side == "bull_reclaim":
            mask &= gap.le(-gap_threshold) & vwap_side.gt(0) & close.lt(prev_close)
        else:
            mask &= gap.ge(gap_threshold) & vwap_side.lt(0) & close.gt(prev_close)
    elif spec["family"] == "red_green_move":
        gap_threshold = float(spec.get("gap_threshold", 0.0))
        prev_close = features[:, fmap[f"{sig}___prev_day_close"]]
        if side == "bull_reclaim":
            mask &= gap.le(-gap_threshold) & close.gt(prev_close) & vwap_side.gt(0)
        else:
            mask &= gap.ge(gap_threshold) & close.lt(prev_close) & vwap_side.lt(0)
    elif spec["family"] == "ten_am_reversal":
        shock = float(spec.get("shock_return", 0.01))
        if side == "bull_reclaim":
            mask &= sess_ret.le(-shock) & vwap_side.gt(-0.001)
        else:
            mask &= sess_ret.ge(shock) & vwap_side.lt(0.001)
    elif spec["family"] == "power_hour_continuation":
        threshold = float(spec.get("threshold", 0.005))
        pre_hi = features[:, fmap[f"{sig}___pre_power_high"]]
        pre_lo = features[:, fmap[f"{sig}___pre_power_low"]]
        if bull:
            mask &= sess_ret.ge(threshold) & vwap_side.gt(0) & close.gt(pre_hi)
        else:
            mask &= sess_ret.le(-threshold) & vwap_side.lt(0) & close.lt(pre_lo)
    elif spec["family"] == "leader_lagger_semiconductor_scalp":
        threshold = float(spec.get("threshold", 0.005))
        smh_ret = features[:, fmap["SMH___session_return"]]
        leader_ret = features[:, fmap[f"{sig}___session_return"]]
        if bull:
            mask &= leader_ret.ge(threshold) & smh_ret.ge(0) & _confirmation_filter(spec, features, fmap, True)
        else:
            mask &= leader_ret.le(-threshold) & smh_ret.le(0) & _confirmation_filter(spec, features, fmap, False)
    elif spec["family"] == "failed_breakout_breakdown":
        rng = int(spec["range_minutes"])
        hi = features[:, fmap[f"{sig}___or_high_{rng}"]]
        lo = features[:, fmap[f"{sig}___or_low_{rng}"]]
        if bull:
            mask &= low.lt(lo) & close.gt(lo)
        else:
            mask &= high.gt(hi) & close.lt(hi)
    elif spec["family"] == "vwap_magnet_chop_scalp":
        ext = float(spec.get("extension_bps", 100.0)) / 10000.0
        trend_abs = sess_ret.abs()
        if bull:
            mask &= vwap_side.le(-ext) & relvol.lt(1.25) & trend_abs.lt(0.025)
        else:
            mask &= vwap_side.ge(ext) & relvol.lt(1.25) & trend_abs.lt(0.025)
    elif spec["family"] == "prior_session_high_low_break":
        buf = float(spec.get("buffer_bps", 0.0)) / 10000.0
        prev_hi = features[:, fmap[f"{sig}___prev_day_high"]]
        prev_lo = features[:, fmap[f"{sig}___prev_day_low"]]
        if bull:
            mask &= close.gt(prev_hi * (1.0 + buf)) & vwap_side.gt(0)
        else:
            mask &= close.lt(prev_lo * (1.0 - buf)) & vwap_side.lt(0)
    elif spec["family"] == "relative_strength_rotation_trade":
        spread_threshold = float(spec.get("spread_threshold", 0.005))
        smh_ret = features[:, fmap["SMH___session_return"]]
        qqq_ret = features[:, fmap["QQQ___session_return"]]
        if trade in {"SOXL", "SOXS"}:
            rel = smh_ret - qqq_ret
        else:
            rel = qqq_ret - smh_ret
        if bull:
            mask &= rel.ge(spread_threshold) & vwap_side.gt(0)
        else:
            mask &= rel.le(-spread_threshold) & vwap_side.lt(0)
    return mask


def _confirmation_filter(spec: dict[str, Any], features: torch.Tensor, fmap: dict[str, int], bull: bool) -> torch.Tensor:
    mode = str(spec.get("confirmation", "none"))
    if mode == "none":
        return torch.ones(features.shape[0], dtype=torch.bool, device=features.device)
    threshold = float(spec.get("confirm_threshold", 0.0))
    vwap_hits = []
    return_hits = []
    for leader in CHIP_LEADERS:
        vwap_side = features[:, fmap[f"{leader}___close_vs_vwap"]]
        sess_ret = features[:, fmap[f"{leader}___session_return"]]
        if bull:
            vwap_hits.append(vwap_side.gt(0))
            return_hits.append(sess_ret.ge(threshold))
        else:
            vwap_hits.append(vwap_side.lt(0))
            return_hits.append(sess_ret.le(-threshold))
    vwap_count = torch.stack(vwap_hits, dim=1).sum(dim=1)
    return_count = torch.stack(return_hits, dim=1).sum(dim=1)
    if mode == "leaders_2of3_vwap":
        return vwap_count.ge(2)
    if mode == "leaders_3of3_vwap":
        return vwap_count.ge(3)
    if mode == "leaders_2of3_return":
        return return_count.ge(2)
    if mode == "leaders_2of3_vwap_and_return":
        return vwap_count.ge(2) & return_count.ge(2)
    raise ValueError(f"Unsupported confirmation mode: {mode}")


def _vwap_filter(spec: dict[str, Any], features: torch.Tensor, fmap: dict[str, int], sig: str, trade: str, bull: bool) -> torch.Tensor:
    mode = spec.get("vwap_filter", "signal")
    out = torch.ones(features.shape[0], dtype=torch.bool, device=features.device)
    if mode in {"signal", "both"}:
        side = features[:, fmap[f"{sig}___close_vs_vwap"]]
        out &= side.gt(0) if bull else side.lt(0)
    if mode in {"trade", "both"}:
        side = features[:, fmap[f"{trade}___close_vs_vwap"]]
        out &= side.gt(0) if bull else side.lt(0)
    return out


def _metrics_np(returns: np.ndarray, timestamps: np.ndarray | None) -> dict[str, Any]:
    returns = returns[np.isfinite(returns)]
    if len(returns) == 0:
        return {"trades": 0.0, "decision_points": 0.0, "win_rate": 0.0, "avg_return": 0.0, "log_total_return": 0.0, "total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0, "_raw_returns": None, "_timestamps": None}
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
        "trades": float(len(returns)),
        "decision_points": float(len(returns)),
        "win_rate": float((returns > 0).mean()),
        "avg_return": float(returns.mean()),
        "log_total_return": log_total,
        "total_return": float(np.exp(log_total) - 1.0) if log_total < 700 else float("inf"),
        "cagr": float(np.exp(log_total * 365.25 / days) - 1.0) if log_total < 700 else float("inf"),
        "max_drawdown": float(dd.min()),
        "_raw_returns": returns,
        "_timestamps": timestamps,
    }


def _select_trade_bases(leaderboard: pd.DataFrame, cost_summary: pd.DataFrame, keep: int) -> set[str]:
    buckets = []
    for cost in [0.0, 2.0, 5.0, 10.0]:
        sub = leaderboard[(leaderboard["cost_bps_per_side"] == cost) & (leaderboard["trades"] >= 25)]
        buckets.append(sub.sort_values(["cagr", "log_total_return"], ascending=False).drop_duplicates("base_candidate_id").head(max(40, keep // 4)))
        buckets.append(sub[(sub["cagr"] >= 0.20) & (sub["max_drawdown"] >= -0.60)].drop_duplicates("base_candidate_id").head(max(40, keep // 4)))
    if not cost_summary.empty:
        ids = cost_summary[(cost_summary["trades"] >= 25) & (cost_summary["max_profitable_cost_bps_per_side"] >= 2.0)].sort_values(["max_profitable_cost_bps_per_side", "best_cagr"], ascending=False).head(keep)["base_candidate_id"]
        buckets.append(leaderboard[leaderboard["base_candidate_id"].isin(ids)].drop_duplicates("base_candidate_id"))
    selected = pd.concat([b for b in buckets if not b.empty], ignore_index=True).drop_duplicates("base_candidate_id")
    return set(selected.head(keep)["base_candidate_id"].astype(str))


def _rebuild_trades(data: pd.DataFrame, specs: list[dict[str, Any]], selected_bases: set[str], feature_names: list[str]) -> pd.DataFrame:
    parts = []
    fmap = {name: i for i, name in enumerate(feature_names)}
    features = torch.as_tensor(data[feature_names].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=np.float32))
    trade_codes = torch.as_tensor(data["_trade_code"].to_numpy(dtype=np.int64))
    for spec in specs:
        base = _base_id(spec)
        if base not in selected_bases:
            continue
        mask = _mask_for_spec(spec, features, fmap, trade_codes).numpy()
        if not mask.any():
            continue
        horizon = spec["horizon"]
        ret_col = "ret_close" if horizon == "close" else f"ret_{int(horizon)}"
        d = data.loc[mask, ["symbol", "timestamp", "open", "high", "low", "close", "_session_close_ts", ret_col]].copy()
        d = d.rename(columns={ret_col: "source_return"})
        d["candidate_id"] = base
        d["rank_formula_id"] = int(spec["spec_id"])
        d["top_n"] = 1
        d["discovery_cost_bps_per_side"] = 0.0
        d["entry_ts"] = d["timestamp"]
        if horizon == "close":
            d["exit_ts"] = d["_session_close_ts"]
        else:
            d["exit_ts"] = d["entry_ts"] + pd.to_timedelta(5 * int(horizon), unit="m")
        d = d.drop(columns=["_session_close_ts"])
        d["entry_ref_price"] = d["close"]
        d["exit_ref_price"] = d["close"] * (1.0 + d["source_return"].astype(float))
        d["gross_source_return"] = d["source_return"]
        parts.append(d)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _horizon_index(h: Any) -> int:
    vals = [1, 2, 3, 4, 6, 12, 24, 48]
    return 8 if h == "close" else vals.index(int(h))


def _stop_bps(model: Any) -> float:
    return {"tight_50bps": 50.0, "wide_150bps": 150.0, "range_mid": 100.0, "vwap": 75.0, "vwap_or_ema": 75.0, "lod_hod_or_vwap": 125.0, "hard_stop": 100.0}.get(str(model), 0.0)


def _target_bps(family: Any) -> float:
    return {"extreme_move_mean_reversion": 100.0, "vwap_trend_pullback": 150.0}.get(str(family), 0.0)


def _base_id(spec: dict[str, Any]) -> str:
    return f"lev{int(spec['spec_id']):07d}"


def _checkpoint_valid(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        cols = set(pd.read_csv(path, nrows=0).columns)
    except Exception:
        return False
    return {"candidate_id", "base_candidate_id", "cagr", "max_drawdown", "cost_bps_per_side"}.issubset(cols)


def _progress(kind: str, batch_idx: int, batches: int, done: int, total: int, t0: float, device: torch.device) -> str:
    elapsed = max(time.perf_counter() - t0, 1e-9)
    rate = done / elapsed
    eta = (total - done) / rate if rate > 0 else 0.0
    gpu_mem = torch.cuda.max_memory_allocated() / 1024**3 if device.type == "cuda" else 0.0
    return f"{kind} batch={batch_idx}/{batches} done={done}/{total} rate_specs_sec={rate:.2f} eta_min={eta / 60:.1f} gpu_mem_peak_gb={gpu_mem:.3f}"
