from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch

from ar_pipeline.engines.cuda_discovery import summarize_cost_sensitivity, write_report
from ar_pipeline.execution import WorkloadInfo
from ar_pipeline.strategies.leveraged_etf_intraday import (
    _checkpoint_valid,
    _feature_names,
    _horizon_index,
    _load_scan_frame,
    _metrics_gpu,
    _progress,
    _stop_bps,
    _target_bps,
    _valid_position_stats,
)


TRADE_ASSETS = ["SOXL", "SOXS"]
SIGNAL_ASSETS = ["SMH", "SOXL", "SOXS", "QQQ", "NVDA", "AMD", "AVGO"]
BAR_MINUTES = 5
FAMILIES = [
    "daily_pick_regime",
    "daily_pick_orb",
    "daily_pick_vwap",
    "daily_pick_leader_vote",
    "daily_pick_gap_reversal",
    "paired_orb_breakout",
    "paired_vwap_reclaim",
    "paired_vwap_rejection",
    "paired_extreme_reversion",
    "paired_power_hour",
    "paired_leader_lagger",
    "paired_close_momentum",
]


def estimate_workload(config: dict[str, Any]) -> WorkloadInfo:
    return WorkloadInfo(
        pattern="soxl_soxs_pair_intraday",
        preferred_device="cuda",
        supports_cuda=True,
        supports_cpu=True,
        supports_batch_autotune=False,
        estimated_rows=None,
        estimated_candidates=len(_build_specs(config.get("scan", {}))) * len(config.get("scan", {}).get("cost_bps_per_side_grid", [0.0])),
    )


def run(config: dict[str, Any], output_dir: Path) -> dict[str, str]:
    t0 = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "progress.log"
    scan = config.get("scan", {})
    specs = _build_specs(scan)
    costs = [float(x) for x in scan.get("cost_bps_per_side_grid", [0.0, 3.0, 5.0, 10.0, 25.0, 50.0])]
    batch_size = int(scan.get("batch_size", 512))
    keep_trades_for = int(scan.get("keep_trades_for_top", 3000))
    resume = bool(scan.get("resume", True))
    device = torch.device("cuda" if str(scan.get("device", "cuda")).lower() == "cuda" and torch.cuda.is_available() else "cpu")
    if str(scan.get("execution", {}).get("require_accelerated", False)).lower() in {"true", "1"} and device.type != "cuda":
        raise RuntimeError("soxl_soxs_pair_intraday requires CUDA for this run")
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()

    def log(message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with progress_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    data = _load_scan_frame(config)
    data = data[data["symbol"].isin(TRADE_ASSETS)].copy().reset_index(drop=True)
    feature_names = _feature_names()
    feature_np = data[feature_names].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=np.float32)
    ret_cols = [f"ret_{h}" for h in [1, 2, 3, 4, 6, 12, 24, 48]] + ["ret_close"]
    mae_cols = [f"mae_{h}" for h in [1, 2, 3, 4, 6, 12, 24, 48]]
    mfe_cols = [f"mfe_{h}" for h in [1, 2, 3, 4, 6, 12, 24, 48]]
    returns_np = data[ret_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=np.float32)
    mae_np = data[mae_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=np.float32)
    mfe_np = data[mfe_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=np.float32)
    trade_code_np = data["symbol"].map({"SOXL": 0, "SOXS": 1}).to_numpy(dtype=np.int64)
    timestamps = pd.to_datetime(data["timestamp"], utc=True).to_numpy()

    features = torch.as_tensor(feature_np, device=device)
    returns = torch.as_tensor(returns_np, device=device)
    maes = torch.as_tensor(mae_np, device=device)
    mfes = torch.as_tensor(mfe_np, device=device)
    trade_codes = torch.as_tensor(trade_code_np, device=device)
    timestamp_ns = timestamps.astype("datetime64[ns]").astype(np.int64, copy=False)
    fmap = {name: i for i, name in enumerate(feature_names)}

    rows: list[pd.DataFrame] = []
    total_batches = math.ceil(len(specs) / batch_size)
    eval_t0 = time.perf_counter()
    log(f"start soxl_soxs_pair_intraday rows={len(data)} specs={len(specs)} batches={total_batches} device={device} cost_grid={costs}")
    for batch_idx, start in enumerate(range(0, len(specs), batch_size), start=1):
        stop = min(start + batch_size, len(specs))
        batch_path = checkpoint_dir / f"batch_{batch_idx:05d}_{start}_{stop}.csv"
        if resume and _checkpoint_valid(batch_path):
            rows.append(pd.read_csv(batch_path))
            log(_progress("resume", batch_idx, total_batches, stop, len(specs), eval_t0, device))
            continue
        result = _evaluate_batch(specs[start:stop], start, features, returns, maes, mfes, trade_codes, timestamp_ns, fmap, costs)
        batch_df = pd.DataFrame(result)
        tmp = batch_path.with_suffix(".csv.tmp")
        batch_df.to_csv(tmp, index=False)
        tmp.replace(batch_path)
        rows.append(batch_df)
        log(_progress("batch", batch_idx, total_batches, stop, len(specs), eval_t0, device))

    leaderboard = pd.concat(rows, ignore_index=True).sort_values(["cagr", "log_total_return"], ascending=False).reset_index(drop=True)
    leaderboard.to_csv(output_dir / "leaderboard.csv", index=False)
    leaderboard.to_parquet(output_dir / "leaderboard.parquet", index=False)
    cost_summary = summarize_cost_sensitivity(leaderboard)
    cost_summary.to_csv(output_dir / "cost_sensitivity.csv", index=False)
    selected = _select_trade_bases(leaderboard, cost_summary, keep_trades_for)
    trades = _rebuild_trades(data, specs, selected, feature_names)
    trades.to_parquet(output_dir / "discovery_trades.parquet", index=False)
    (output_dir / "strategy_specs.json").write_text(json.dumps(specs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metadata = {
        "rows": int(len(data)),
        "specs": int(len(specs)),
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
    log(f"done soxl_soxs_pair_intraday candidate_rows={len(leaderboard)} elapsed_sec={metadata['total_elapsed_seconds']}")
    return {
        "leaderboard": str(output_dir / "leaderboard.csv"),
        "leaderboard_parquet": str(output_dir / "leaderboard.parquet"),
        "cost_sensitivity": str(output_dir / "cost_sensitivity.csv"),
        "trades": str(output_dir / "discovery_trades.parquet"),
        "report": str(output_dir / "discovery_report.md"),
        "strategy_specs": str(output_dir / "strategy_specs.json"),
    }


def _build_specs(scan: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if scan.get("daily_selector_expansion"):
        return _build_daily_selector_expansion_specs(scan)
    if scan.get("focused_second_pass"):
        return _build_focused_second_pass_specs(scan)
    signals = scan.get("signal_assets", SIGNAL_ASSETS)
    families = set(scan.get("families", FAMILIES))
    horizons = scan.get("horizons", [1, 2, 3, 6, 12, 24, "close"])

    def add(family: str, **kw: Any) -> None:
        if family in families:
            specs.append({"spec_id": len(specs), "family": family, **kw})

    for sig in signals:
        for entry_min in [0, 5, 15, 30, 60, 90, 120, 180, 240, 330]:
            for threshold in [0.0, 0.0015, 0.0025, 0.005, 0.01, 0.02]:
                for rule in ["session_return", "vwap_side", "gap", "qqq_smh_spread", "soxl_soxs_spread"]:
                    for horizon in ["close", 12, 24, 48]:
                        add("daily_pick_regime", signal_asset=sig, start_minute=entry_min, end_minute=entry_min, threshold=threshold, rule=rule, horizon=horizon, stop_model="none")
        for rng in [5, 10, 15, 30]:
            for start_min, end_min in [(rng, rng), (rng, 90), (15, 120), (30, 180)]:
                for buffer_bps in [0, 5, 15, 30]:
                    for horizon in horizons:
                        add("daily_pick_orb", signal_asset=sig, range_minutes=rng, start_minute=start_min, end_minute=end_min, buffer_bps=buffer_bps, horizon=horizon, stop_model="range_mid")
                        add("paired_orb_breakout", signal_asset=sig, range_minutes=rng, start_minute=start_min, end_minute=end_min, buffer_bps=buffer_bps, horizon=horizon, stop_model="range_mid")
        for start_min, end_min in [(15, 120), (30, 180), (60, 240), (120, 330)]:
            for threshold in [0.0, 0.0015, 0.0025, 0.005, 0.01, 0.02]:
                for horizon in horizons:
                    add("daily_pick_vwap", signal_asset=sig, start_minute=start_min, end_minute=start_min, threshold=threshold, horizon=horizon, stop_model="vwap")
                    add("paired_vwap_reclaim", signal_asset=sig, start_minute=start_min, end_minute=end_min, threshold=threshold, horizon=horizon, stop_model="vwap")
                    add("paired_vwap_rejection", signal_asset=sig, start_minute=start_min, end_minute=end_min, threshold=threshold, horizon=horizon, stop_model="vwap")
            for extension_bps in [50, 100, 150, 250, 400]:
                for horizon in [1, 2, 3, 6, 12]:
                    add("paired_extreme_reversion", signal_asset=sig, start_minute=start_min, end_minute=end_min, extension_bps=extension_bps, horizon=horizon, stop_model="hard_stop")
        for start_min, end_min in [(25, 60), (30, 90), (45, 120), (330, 385)]:
            for threshold in [0.0025, 0.005, 0.01, 0.02, 0.04]:
                for horizon in [1, 2, 3, 6, 12, "close"]:
                    add("daily_pick_gap_reversal", signal_asset=sig, start_minute=start_min, end_minute=end_min, threshold=threshold, horizon=horizon, stop_model="lod_hod_or_vwap")
                    add("paired_power_hour", signal_asset=sig, start_minute=max(start_min, 300), end_minute=385, threshold=threshold, horizon=horizon, stop_model="afternoon_vwap")
                    add("paired_close_momentum", signal_asset=sig, start_minute=start_min, end_minute=end_min, threshold=threshold, horizon=horizon, stop_model="afternoon_vwap")

    for vote in ["leaders_2of3_return", "leaders_2of3_vwap", "leaders_2of3_vwap_and_return"]:
        for start_min, end_min in [(0, 0), (15, 15), (30, 30), (60, 60), (15, 120), (60, 240)]:
            for threshold in [0.0, 0.0025, 0.005, 0.01, 0.02]:
                for horizon in horizons:
                    add("daily_pick_leader_vote", signal_asset="SMH", start_minute=start_min, end_minute=end_min, vote=vote, threshold=threshold, horizon=horizon, stop_model="vwap")
                    add("paired_leader_lagger", signal_asset="SMH", start_minute=start_min, end_minute=end_min, vote=vote, threshold=threshold, horizon=horizon, stop_model="tight_50bps")

    max_specs = int(scan.get("max_specs", 0) or 0)
    if max_specs > 0 and len(specs) > max_specs:
        specs = _budget_specs(specs, max_specs)
    for i, spec in enumerate(specs):
        spec["spec_id"] = i
    return specs


def _build_daily_selector_expansion_specs(scan: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    horizons = scan.get("horizons", [6, 12, 24, 48, "close"])
    starts = scan.get("daily_selector_start_minutes", [0, 5, 10, 15, 30, 45, 60, 90, 120])
    score_models = [
        "soxl_session",
        "soxl_soxs_spread",
        "smh_session",
        "qqq_session",
        "smh_qqq_blend",
        "leader_avg",
        "leader_vote_balance",
        "semis_full_blend",
        "risk_on_blend",
        "soxl_vwap",
        "soxl_soxs_vwap_spread",
        "smh_vwap_return_blend",
        "gap_follow_soxl",
        "gap_follow_smh",
        "gap_fade_soxl",
        "gap_fade_smh",
        "prev_day_follow_smh",
        "prev_day_fade_smh",
        "bar_momentum_stack_soxl",
        "bar_momentum_stack_smh",
    ]
    min_abs_scores = [0.0, 0.00025, 0.0005, 0.001, 0.0015, 0.0025, 0.005]
    vwap_filters = ["none", "trade_vwap_agree", "smh_vwap_agree", "qqq_vwap_agree", "smh_qqq_vwap_agree"]
    rsi_filters = ["none", "avoid_extreme"]
    gap_caps = [None, 0.02, 0.035, 0.05]

    def add(**kw: Any) -> None:
        specs.append({"spec_id": len(specs), "family": "daily_score_selector", **kw})

    for start_min in starts:
        for horizon in horizons:
            for model in score_models:
                for min_abs in min_abs_scores:
                    for invert in [False, True]:
                        for vwap_filter in vwap_filters:
                            for rsi_filter in rsi_filters:
                                for max_gap_abs in gap_caps:
                                    add(
                                        signal_asset="SMH",
                                        start_minute=start_min,
                                        end_minute=start_min,
                                        horizon=horizon,
                                        score_model=model,
                                        min_abs_score=min_abs,
                                        invert=invert,
                                        vwap_filter=vwap_filter,
                                        rsi_filter=rsi_filter,
                                        max_gap_abs=max_gap_abs,
                                        stop_model="none",
                                    )

    max_specs = int(scan.get("max_specs", 0) or 0)
    if max_specs > 0 and len(specs) > max_specs:
        specs = _budget_specs(specs, max_specs)
    for i, spec in enumerate(specs):
        spec["spec_id"] = i
    return specs


def _build_focused_second_pass_specs(scan: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    horizons = scan.get("horizons", [6, 12, 24, 48, "close"])
    daily_rules = ["session_return", "soxl_soxs_spread", "vwap_side"]
    confirm_modes = [
        "none",
        "smh_vwap_agree",
        "qqq_vwap_agree",
        "smh_and_qqq_vwap_agree",
        "leaders_2of3_return",
        "leaders_2of3_vwap",
        "leaders_2of3_vwap_and_return",
    ]
    trade_vwap_modes = ["none", "trade_vwap_agree"]
    volume_filters = ["none", "relvol_ge_1", "relvol_ge_1p25", "relvol_le_2"]
    rsi_filters = ["none", "avoid_extreme", "momentum_ok"]
    start_minutes = [0, 5, 10, 15, 30, 45, 60]
    thresholds = [0.0, 0.0005, 0.001, 0.0015, 0.0025, 0.005, 0.0075, 0.01]
    max_gap_abs_values = [None, 0.01, 0.02, 0.035]

    def add(family: str, **kw: Any) -> None:
        specs.append({"spec_id": len(specs), "family": family, **kw})

    for sig in ["SOXL", "SMH", "SOXS", "QQQ"]:
        for start_min in start_minutes:
            for horizon in horizons:
                for rule in daily_rules:
                    for threshold in thresholds:
                        for confirm in confirm_modes:
                            for trade_vwap in trade_vwap_modes:
                                for vol_filter in volume_filters:
                                    for rsi_filter in rsi_filters:
                                        for max_gap_abs in max_gap_abs_values:
                                            add(
                                                "focused_daily_regime_filter",
                                                signal_asset=sig,
                                                start_minute=start_min,
                                                end_minute=start_min,
                                                horizon=horizon,
                                                rule=rule,
                                                threshold=threshold,
                                                confirm=confirm,
                                                trade_vwap=trade_vwap,
                                                volume_filter=vol_filter,
                                                rsi_filter=rsi_filter,
                                                max_gap_abs=max_gap_abs,
                                                stop_model="none",
                                            )

    for vote in ["leaders_2of3_return", "leaders_2of3_vwap", "leaders_2of3_vwap_and_return"]:
        for start_min in start_minutes:
            for horizon in horizons:
                for threshold in [0.0, 0.001, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02]:
                    for confirm in ["none", "smh_vwap_agree", "qqq_vwap_agree", "smh_and_qqq_vwap_agree"]:
                        for trade_vwap in trade_vwap_modes:
                            for vol_filter in volume_filters:
                                for rsi_filter in rsi_filters:
                                    add(
                                        "focused_leader_vote_filter",
                                        signal_asset="SMH",
                                        start_minute=start_min,
                                        end_minute=start_min,
                                        horizon=horizon,
                                        vote=vote,
                                        threshold=threshold,
                                        confirm=confirm,
                                        trade_vwap=trade_vwap,
                                        volume_filter=vol_filter,
                                        rsi_filter=rsi_filter,
                                        stop_model="none",
                                    )

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
    selected: list[dict[str, Any]] = []
    per_family = max(1, max_specs // max(len(by_family), 1))
    for family in sorted(by_family):
        group = by_family[family]
        selected.extend(_even_sample(group, min(len(group), per_family)))
    if len(selected) < max_specs:
        existing = {id(x) for x in selected}
        remaining = [x for x in specs if id(x) not in existing]
        selected.extend(_even_sample(remaining, max_specs - len(selected)))
    return selected[:max_specs]


def _even_sample(items: list[dict[str, Any]], quota: int) -> list[dict[str, Any]]:
    if quota >= len(items):
        return list(items)
    if quota <= 0:
        return []
    idxs = np.linspace(0, len(items) - 1, quota, dtype=np.int64)
    return [items[int(i)] for i in idxs]


def _evaluate_batch(
    specs: list[dict[str, Any]],
    start_id: int,
    features: torch.Tensor,
    returns: torch.Tensor,
    maes: torch.Tensor,
    mfes: torch.Tensor,
    trade_codes: torch.Tensor,
    timestamp_ns: np.ndarray,
    fmap: dict[str, int],
    costs: list[float],
) -> list[dict[str, Any]]:
    masks = [_mask_for_spec(spec, features, fmap, trade_codes) for spec in specs]
    mask = torch.stack(masks, dim=1)
    horizon_idx = torch.as_tensor([_horizon_index(s["horizon"]) for s in specs], device=features.device)
    selected_returns = returns[:, horizon_idx]
    selected_mae = maes[:, torch.clamp(horizon_idx, max=7)]
    selected_mfe = mfes[:, torch.clamp(horizon_idx, max=7)]
    stop_tensor = torch.as_tensor([_stop_bps(s.get("stop_model")) for s in specs], device=features.device) / 10000.0
    target = torch.as_tensor([_target_bps(s.get("family")) for s in specs], device=features.device) / 10000.0
    gross = selected_returns
    stopped = (stop_tensor > 0).unsqueeze(0) & (selected_mae <= -stop_tensor.unsqueeze(0))
    targeted = (target > 0).unsqueeze(0) & (selected_mfe >= target.unsqueeze(0))
    gross = torch.where(stopped, -stop_tensor.unsqueeze(0), torch.where(targeted, target.unsqueeze(0), gross))
    gross = torch.where(mask, gross, torch.nan)
    valid = torch.isfinite(gross)
    counts, first_idx, last_idx = _valid_position_stats(valid)
    rows: list[dict[str, Any]] = []
    for cost in costs:
        metrics = _metrics_gpu(gross, valid, counts, first_idx, last_idx, timestamp_ns, float(cost))
        for local_i, spec in enumerate(specs):
            base = _base_id(spec)
            rows.append({
                "candidate_id": f"{base}_c{str(cost).replace('.', 'p')}",
                "base_candidate_id": base,
                "family": spec["family"],
                "formula_id": int(start_id + local_i),
                "top_n": 1,
                "horizon": str(spec["horizon"]),
                "cost_bps_per_side": float(cost),
                "spec": json.dumps(spec, sort_keys=True),
                **{k: v[local_i] for k, v in metrics.items()},
            })
    return rows


def _mask_for_spec(spec: dict[str, Any], features: torch.Tensor, fmap: dict[str, int], trade_codes: torch.Tensor) -> torch.Tensor:
    sig = str(spec.get("signal_asset", "SMH"))
    minute = features[:, fmap["_minute"]]
    in_time = minute.ge(float(spec.get("start_minute", 0))) & minute.le(float(spec.get("end_minute", 385)))
    horizon = spec.get("horizon")
    if horizon != "close":
        in_time &= minute + (BAR_MINUTES * (float(horizon) + 1.0)) <= 390.0
    else:
        in_time &= minute <= 380.0
    soxl = trade_codes.eq(0)
    soxs = trade_codes.eq(1)
    bull, bear = _direction_conditions(spec, sig, features, fmap)
    return in_time & ((soxl & bull) | (soxs & bear))


def _direction_conditions(spec: dict[str, Any], sig: str, features: torch.Tensor, fmap: dict[str, int]) -> tuple[torch.Tensor, torch.Tensor]:
    family = str(spec["family"])
    close = features[:, fmap[f"{sig}__close"]]
    high = features[:, fmap[f"{sig}__high"]]
    low = features[:, fmap[f"{sig}__low"]]
    vwap_side = features[:, fmap[f"{sig}___close_vs_vwap"]]
    sess_ret = features[:, fmap[f"{sig}___session_return"]]
    gap = features[:, fmap[f"{sig}___gap_return"]]
    relvol = features[:, fmap[f"{sig}__relative_volume_20"]]
    threshold = float(spec.get("threshold", 0.0))
    if family == "daily_score_selector":
        score = _daily_selector_score(str(spec.get("score_model", "soxl_soxs_spread")), features, fmap)
        if bool(spec.get("invert", False)):
            score = -score
        min_abs = float(spec.get("min_abs_score", 0.0))
        bull = score.ge(min_abs)
        bear = score.lt(-min_abs)
        bull, bear = _apply_daily_selector_filters(spec, features, fmap, bull, bear)
        return bull, bear
    if family in {"daily_pick_regime", "focused_daily_regime_filter"}:
        rule = str(spec.get("rule", "session_return"))
        if rule == "vwap_side":
            score = vwap_side
        elif rule == "gap":
            score = gap
        elif rule == "qqq_smh_spread":
            score = features[:, fmap["SMH___session_return"]] - features[:, fmap["QQQ___session_return"]]
        elif rule == "soxl_soxs_spread":
            score = features[:, fmap["SOXL___session_return"]] - features[:, fmap["SOXS___session_return"]]
        else:
            score = sess_ret
        bull = score.ge(threshold)
        bear = score.lt(-threshold if threshold > 0 else threshold)
        if family == "focused_daily_regime_filter":
            bull, bear = _apply_pair_filters(spec, sig, features, fmap, bull, bear)
        return bull, bear
    if family in {"daily_pick_orb", "paired_orb_breakout"}:
        rng = int(spec.get("range_minutes", 15))
        buf = float(spec.get("buffer_bps", 0.0)) / 10000.0
        hi = features[:, fmap[f"{sig}___or_high_{rng}"]]
        lo = features[:, fmap[f"{sig}___or_low_{rng}"]]
        return close.gt(hi * (1.0 + buf)) & vwap_side.gt(0), close.lt(lo * (1.0 - buf)) & vwap_side.lt(0)
    if family in {"daily_pick_vwap", "paired_vwap_reclaim"}:
        return sess_ret.ge(threshold) & vwap_side.gt(0), sess_ret.le(-threshold) & vwap_side.lt(0)
    if family == "paired_vwap_rejection":
        prev = features[:, fmap[f"{sig}___prev_close_vs_vwap"]]
        return prev.ge(0) & vwap_side.ge(0) & vwap_side.le(max(threshold, 0.001)), prev.le(0) & vwap_side.le(0) & vwap_side.ge(-max(threshold, 0.001))
    if family == "paired_extreme_reversion":
        ext = float(spec.get("extension_bps", 100.0)) / 10000.0
        return vwap_side.le(-ext) & relvol.lt(1.25), vwap_side.ge(ext) & relvol.lt(1.25)
    if family == "daily_pick_gap_reversal":
        return gap.le(-threshold) & vwap_side.gt(-0.001), gap.ge(threshold) & vwap_side.lt(0.001)
    if family in {"daily_pick_leader_vote", "paired_leader_lagger", "focused_leader_vote_filter"}:
        bull = _leader_vote(features, fmap, True, str(spec.get("vote", "leaders_2of3_return")), threshold)
        bear = _leader_vote(features, fmap, False, str(spec.get("vote", "leaders_2of3_return")), threshold)
        if family == "focused_leader_vote_filter":
            bull, bear = _apply_pair_filters(spec, sig, features, fmap, bull, bear)
        return bull, bear
    if family == "paired_power_hour":
        pre_hi = features[:, fmap[f"{sig}___pre_power_high"]]
        pre_lo = features[:, fmap[f"{sig}___pre_power_low"]]
        return sess_ret.ge(threshold) & close.gt(pre_hi) & vwap_side.gt(0), sess_ret.le(-threshold) & close.lt(pre_lo) & vwap_side.lt(0)
    if family == "paired_close_momentum":
        return sess_ret.ge(threshold) & vwap_side.gt(0), sess_ret.le(-threshold) & vwap_side.lt(0)
    zeros = torch.zeros(features.shape[0], dtype=torch.bool, device=features.device)
    return zeros, zeros


def _daily_selector_score(model: str, features: torch.Tensor, fmap: dict[str, int]) -> torch.Tensor:
    soxl_ret = features[:, fmap["SOXL___session_return"]]
    soxs_ret = features[:, fmap["SOXS___session_return"]]
    smh_ret = features[:, fmap["SMH___session_return"]]
    qqq_ret = features[:, fmap["QQQ___session_return"]]
    nvda_ret = features[:, fmap["NVDA___session_return"]]
    amd_ret = features[:, fmap["AMD___session_return"]]
    avgo_ret = features[:, fmap["AVGO___session_return"]]
    leader_avg = (nvda_ret + amd_ret + avgo_ret) / 3.0
    soxl_vwap = features[:, fmap["SOXL___close_vs_vwap"]]
    soxs_vwap = features[:, fmap["SOXS___close_vs_vwap"]]
    smh_vwap = features[:, fmap["SMH___close_vs_vwap"]]
    qqq_vwap = features[:, fmap["QQQ___close_vs_vwap"]]
    if model == "soxl_session":
        return soxl_ret
    if model == "soxl_soxs_spread":
        return soxl_ret - soxs_ret
    if model == "smh_session":
        return smh_ret
    if model == "qqq_session":
        return qqq_ret
    if model == "smh_qqq_blend":
        return 0.65 * smh_ret + 0.35 * qqq_ret
    if model == "leader_avg":
        return leader_avg
    if model == "leader_vote_balance":
        bull = nvda_ret.sign() + amd_ret.sign() + avgo_ret.sign()
        return bull / 3.0
    if model == "semis_full_blend":
        return 0.45 * smh_ret + 0.35 * leader_avg + 0.20 * qqq_ret
    if model == "risk_on_blend":
        return 0.35 * smh_ret + 0.35 * qqq_ret + 0.30 * leader_avg
    if model == "soxl_vwap":
        return soxl_vwap
    if model == "soxl_soxs_vwap_spread":
        return soxl_vwap - soxs_vwap
    if model == "smh_vwap_return_blend":
        return smh_ret + 0.5 * smh_vwap
    if model == "gap_follow_soxl":
        return features[:, fmap["SOXL___gap_return"]]
    if model == "gap_follow_smh":
        return features[:, fmap["SMH___gap_return"]]
    if model == "gap_fade_soxl":
        return -features[:, fmap["SOXL___gap_return"]]
    if model == "gap_fade_smh":
        return -features[:, fmap["SMH___gap_return"]]
    if model == "prev_day_follow_smh":
        return features[:, fmap["SMH___prev_day_return"]]
    if model == "prev_day_fade_smh":
        return -features[:, fmap["SMH___prev_day_return"]]
    if model == "bar_momentum_stack_soxl":
        return (
            features[:, fmap["SOXL___bar_return"]]
            + features[:, fmap["SOXL___prev_bar_return"]]
            + features[:, fmap["SOXL___prev2_bar_return"]]
        )
    if model == "bar_momentum_stack_smh":
        return (
            features[:, fmap["SMH___bar_return"]]
            + features[:, fmap["SMH___prev_bar_return"]]
            + features[:, fmap["SMH___prev2_bar_return"]]
        )
    return soxl_ret - soxs_ret


def _apply_daily_selector_filters(
    spec: dict[str, Any],
    features: torch.Tensor,
    fmap: dict[str, int],
    bull: torch.Tensor,
    bear: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    vwap_filter = str(spec.get("vwap_filter", "none"))
    if vwap_filter == "trade_vwap_agree":
        bull &= features[:, fmap["SOXL___close_vs_vwap"]].gt(0)
        bear &= features[:, fmap["SOXS___close_vs_vwap"]].gt(0)
    elif vwap_filter == "smh_vwap_agree":
        smh = features[:, fmap["SMH___close_vs_vwap"]]
        bull &= smh.gt(0)
        bear &= smh.lt(0)
    elif vwap_filter == "qqq_vwap_agree":
        qqq = features[:, fmap["QQQ___close_vs_vwap"]]
        bull &= qqq.gt(0)
        bear &= qqq.lt(0)
    elif vwap_filter == "smh_qqq_vwap_agree":
        smh = features[:, fmap["SMH___close_vs_vwap"]]
        qqq = features[:, fmap["QQQ___close_vs_vwap"]]
        bull &= smh.gt(0) & qqq.gt(0)
        bear &= smh.lt(0) & qqq.lt(0)

    if str(spec.get("rsi_filter", "none")) == "avoid_extreme":
        soxl_rsi = features[:, fmap["SOXL__rsi_14"]]
        soxs_rsi = features[:, fmap["SOXS__rsi_14"]]
        bull &= soxl_rsi.lt(78)
        bear &= soxs_rsi.lt(78)

    max_gap_abs = spec.get("max_gap_abs")
    if max_gap_abs is not None:
        soxl_gap = features[:, fmap["SOXL___gap_return"]].abs()
        smh_gap = features[:, fmap["SMH___gap_return"]].abs()
        ok = soxl_gap.le(float(max_gap_abs)) & smh_gap.le(float(max_gap_abs))
        bull &= ok
        bear &= ok
    return bull, bear


def _apply_pair_filters(
    spec: dict[str, Any],
    sig: str,
    features: torch.Tensor,
    fmap: dict[str, int],
    bull: torch.Tensor,
    bear: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    confirm = str(spec.get("confirm", "none"))
    if confirm in {"smh_vwap_agree", "smh_and_qqq_vwap_agree"}:
        smh_vwap = features[:, fmap["SMH___close_vs_vwap"]]
        bull &= smh_vwap.gt(0)
        bear &= smh_vwap.lt(0)
    if confirm in {"qqq_vwap_agree", "smh_and_qqq_vwap_agree"}:
        qqq_vwap = features[:, fmap["QQQ___close_vs_vwap"]]
        bull &= qqq_vwap.gt(0)
        bear &= qqq_vwap.lt(0)
    if confirm == "leaders_2of3_return":
        bull &= _leader_vote(features, fmap, True, "leaders_2of3_return", float(spec.get("threshold", 0.0)))
        bear &= _leader_vote(features, fmap, False, "leaders_2of3_return", float(spec.get("threshold", 0.0)))
    elif confirm == "leaders_2of3_vwap":
        bull &= _leader_vote(features, fmap, True, "leaders_2of3_vwap", float(spec.get("threshold", 0.0)))
        bear &= _leader_vote(features, fmap, False, "leaders_2of3_vwap", float(spec.get("threshold", 0.0)))
    elif confirm == "leaders_2of3_vwap_and_return":
        bull &= _leader_vote(features, fmap, True, "leaders_2of3_vwap_and_return", float(spec.get("threshold", 0.0)))
        bear &= _leader_vote(features, fmap, False, "leaders_2of3_vwap_and_return", float(spec.get("threshold", 0.0)))

    if str(spec.get("trade_vwap", "none")) == "trade_vwap_agree":
        soxl_vwap = features[:, fmap["SOXL___close_vs_vwap"]]
        soxs_vwap = features[:, fmap["SOXS___close_vs_vwap"]]
        bull &= soxl_vwap.gt(0)
        bear &= soxs_vwap.gt(0)

    vol_filter = str(spec.get("volume_filter", "none"))
    relvol = features[:, fmap[f"{sig}__relative_volume_20"]]
    if vol_filter == "relvol_ge_1":
        bull &= relvol.ge(1.0)
        bear &= relvol.ge(1.0)
    elif vol_filter == "relvol_ge_1p25":
        bull &= relvol.ge(1.25)
        bear &= relvol.ge(1.25)
    elif vol_filter == "relvol_le_2":
        bull &= relvol.le(2.0)
        bear &= relvol.le(2.0)

    rsi_filter = str(spec.get("rsi_filter", "none"))
    rsi = features[:, fmap[f"{sig}__rsi_14"]]
    if rsi_filter == "avoid_extreme":
        bull &= rsi.lt(75)
        bear &= rsi.gt(25)
    elif rsi_filter == "momentum_ok":
        bull &= rsi.ge(50) & rsi.lt(80)
        bear &= rsi.le(50) & rsi.gt(20)

    max_gap_abs = spec.get("max_gap_abs")
    if max_gap_abs is not None:
        gap = features[:, fmap[f"{sig}___gap_return"]]
        bull &= gap.abs().le(float(max_gap_abs))
        bear &= gap.abs().le(float(max_gap_abs))
    return bull, bear


def _leader_vote(features: torch.Tensor, fmap: dict[str, int], bull: bool, mode: str, threshold: float) -> torch.Tensor:
    leaders = ["NVDA", "AMD", "AVGO"]
    vwap_hits = []
    ret_hits = []
    for leader in leaders:
        vwap_side = features[:, fmap[f"{leader}___close_vs_vwap"]]
        sess_ret = features[:, fmap[f"{leader}___session_return"]]
        if bull:
            vwap_hits.append(vwap_side.gt(0))
            ret_hits.append(sess_ret.ge(threshold))
        else:
            vwap_hits.append(vwap_side.lt(0))
            ret_hits.append(sess_ret.le(-threshold))
    vwap_count = torch.stack(vwap_hits, dim=1).sum(dim=1)
    ret_count = torch.stack(ret_hits, dim=1).sum(dim=1)
    if mode == "leaders_2of3_vwap":
        return vwap_count.ge(2)
    if mode == "leaders_2of3_vwap_and_return":
        return vwap_count.ge(2) & ret_count.ge(2)
    return ret_count.ge(2)


def _select_trade_bases(leaderboard: pd.DataFrame, cost_summary: pd.DataFrame, keep: int) -> set[str]:
    buckets = []
    for cost in [0.0, 3.0, 5.0, 10.0]:
        sub = leaderboard[(leaderboard["cost_bps_per_side"] == cost) & (leaderboard["trades"] >= 250)]
        buckets.append(sub.sort_values(["cagr", "log_total_return"], ascending=False).drop_duplicates("base_candidate_id").head(max(80, keep // 5)))
        buckets.append(sub[(sub["cagr"] >= 0.20) & (sub["max_drawdown"] >= -0.60)].drop_duplicates("base_candidate_id").head(max(80, keep // 5)))
    if not cost_summary.empty:
        ids = cost_summary[(cost_summary["trades"] >= 250) & (cost_summary["max_profitable_cost_bps_per_side"] >= 3.0)].sort_values(["max_profitable_cost_bps_per_side", "best_cagr"], ascending=False).head(keep)["base_candidate_id"]
        buckets.append(leaderboard[leaderboard["base_candidate_id"].isin(ids)].drop_duplicates("base_candidate_id"))
    selected = pd.concat([b for b in buckets if not b.empty], ignore_index=True).drop_duplicates("base_candidate_id")
    return set(selected.head(keep)["base_candidate_id"].astype(str))


def _rebuild_trades(data: pd.DataFrame, specs: list[dict[str, Any]], selected_bases: set[str], feature_names: list[str]) -> pd.DataFrame:
    parts = []
    fmap = {name: i for i, name in enumerate(feature_names)}
    features = torch.as_tensor(data[feature_names].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=np.float32))
    trade_codes = torch.as_tensor(data["symbol"].map({"SOXL": 0, "SOXS": 1}).to_numpy(dtype=np.int64))
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
        d["entry_ts"] = d["timestamp"] + pd.to_timedelta(BAR_MINUTES, unit="m")
        if horizon == "close":
            d["exit_ts"] = d["_session_close_ts"] + pd.to_timedelta(BAR_MINUTES, unit="m")
        else:
            d["exit_ts"] = d["timestamp"] + pd.to_timedelta(BAR_MINUTES * (int(horizon) + 1), unit="m")
        d = d.drop(columns=["_session_close_ts"])
        d["entry_ref_price"] = d["close"]
        d["exit_ref_price"] = d["close"] * (1.0 + d["source_return"].astype(float))
        d["gross_source_return"] = d["source_return"]
        parts.append(d)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _base_id(spec: dict[str, Any]) -> str:
    return f"pair{int(spec['spec_id']):07d}"
