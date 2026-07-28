from __future__ import annotations

import json
from dataclasses import dataclass
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch

from ar_pipeline.data import cross_sectional_feature_ranks, load_bar_screen_frame
from ar_pipeline.contracts import fingerprint
@dataclass(frozen=True)
class DiscoveryResult:
    leaderboard: pd.DataFrame
    trades: pd.DataFrame
    metadata: dict[str, Any]


def run_discovery(config: dict[str, Any], output_dir: Path) -> DiscoveryResult:
    t0 = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "progress.log"
    scan = config.get("scan", {})
    features = list(scan.get("features") or [])
    if not features:
        raise ValueError("scan.features must not be empty")
    holding_bars = int(scan.get("holding_bars", scan.get("horizon", 1)))
    formulas = int(scan.get("formulas", 512))
    batch_size = int(scan.get("batch_size", 256))
    top_ns = [int(x) for x in scan.get("top_ns", [1, 2, 3])]
    seed = int(scan.get("seed", 20260629))
    cost_grid = scan.get("cost_bps_per_side_grid")
    if cost_grid is None:
        cost_grid = [float(scan.get("cost_bps_per_side", 5.0))]
    cost_grid = [float(x) for x in cost_grid]
    device_name = scan.get("device", "cuda")
    device = torch.device("cuda" if device_name == "cuda" and torch.cuda.is_available() else "cpu")
    resume = bool(scan.get("resume", True))
    keep_batch_rows = int(scan.get("keep_checkpoint_rows_per_batch", 5000))
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()

    def log(message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with progress_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    df = load_bar_screen_frame(config)
    load_elapsed = time.perf_counter() - t0
    data_fingerprint = fingerprint(
        {
            "rows": len(df),
            "first_signal": str(df["signal_ts"].min()),
            "last_signal": str(df["signal_ts"].max()),
            "symbols": sorted(df["symbol"].astype(str).unique().tolist()),
            "entry_sum": round(float(df["entry_open"].astype(float).sum()), 8),
            "exit_sum": round(float(df["exit_close"].astype(float).sum()), 8),
        }
    )
    run_fingerprint = fingerprint({"config": config, "data": data_fingerprint})
    checkpoint_dir = output_dir / "checkpoints" / f"run_{run_fingerprint[:16]}"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    eval_t0 = time.perf_counter()
    log(
        "start discovery "
        f"rows={len(df)} formulas={formulas} batch_size={batch_size} "
        f"top_ns={top_ns} cost_grid={cost_grid} device={device}"
    )
    x_np = cross_sectional_feature_ranks(df, features)
    gross_ret_np = df["bar_gross_return"].to_numpy(dtype=np.float32)
    timestamps = df["signal_ts"].to_numpy()
    group_offsets = _group_offsets(timestamps)

    rng = np.random.default_rng(seed)
    weights = rng.normal(size=(formulas, len(features))).astype(np.float32)
    weights /= np.maximum(np.linalg.norm(weights, axis=1, keepdims=True), 1e-9)
    x = torch.as_tensor(x_np, device=device)
    gross_rets = torch.as_tensor(gross_ret_np, device=device)
    group_index, group_valid = _build_group_index(group_offsets, len(df), device)
    if device.type == "cuda" and bool(scan.get("execution", {}).get("benchmark", False)):
        batch_size = _benchmark_and_select_batch_size(
            scan=scan,
            output_dir=output_dir,
            x=x,
            gross_rets=gross_rets,
            weights=weights,
            group_index=group_index,
            group_valid=group_valid,
            top_ns=top_ns,
            current_batch_size=batch_size,
            log=log,
        )
        scan["batch_size"] = batch_size

    batch_paths: list[Path] = []
    trade_parts: list[pd.DataFrame] = []
    keep_trades_for = int(scan.get("keep_trades_for_top", 50))
    total_batches = int(np.ceil(formulas / batch_size))
    completed_formulas = 0
    for batch_idx, start in enumerate(range(0, formulas, batch_size)):
        stop = min(start + batch_size, formulas)
        batch_path = checkpoint_dir / f"batch_{batch_idx:05d}_{start}_{stop}.csv"
        if resume and _checkpoint_valid(batch_path):
            batch_paths.append(batch_path)
            completed_formulas = stop
            log(f"resume batch={batch_idx + 1}/{total_batches} formulas={start}:{stop} checkpoint={batch_path.name}")
            continue
        w = torch.as_tensor(weights[start:stop].T, device=device)
        scores = x @ w
        top_return_mats = _top_return_matrices_dense(scores, gross_rets, group_index, group_valid, top_ns)
        rows: list[dict[str, Any]] = []
        formula_ids = list(range(start, stop))
        for top_n in top_ns:
            rows.extend(
                _metric_rows_for_batch(
                    selected_gross_ret=top_return_mats[top_n],
                    formula_ids=formula_ids,
                    top_n=top_n,
                    holding_bars=holding_bars,
                    annualization_years=_annualization_years(df["signal_ts"]),
                    cost_grid=cost_grid,
                    family=scan.get("family", "unclassified"),
                )
            )
        batch_df = pd.DataFrame(rows).sort_values(["cagr", "log_total_return"], ascending=False)
        if keep_batch_rows > 0 and len(batch_df) > keep_batch_rows:
            batch_df = batch_df.head(keep_batch_rows).copy()
        tmp_path = batch_path.with_suffix(batch_path.suffix + ".tmp")
        batch_df.to_csv(tmp_path, index=False)
        tmp_path.replace(batch_path)
        batch_paths.append(batch_path)
        completed_formulas = stop
        elapsed = max(time.perf_counter() - eval_t0, 1e-9)
        rate = completed_formulas / elapsed
        remaining = max(formulas - completed_formulas, 0)
        eta_seconds = remaining / rate if rate > 0 else float("inf")
        gpu_mem = torch.cuda.max_memory_allocated() / 1024**3 if device.type == "cuda" else 0.0
        log(
            f"batch={batch_idx + 1}/{total_batches} formulas={start}:{stop} "
            f"rows={len(batch_df)} done={completed_formulas}/{formulas} "
            f"rate_formulas_sec={rate:.2f} eta_min={eta_seconds / 60:.1f} "
            f"gpu_mem_peak_gb={gpu_mem:.3f}"
        )
        if device.type == "cuda":
            torch.cuda.empty_cache()

    if not batch_paths:
        raise RuntimeError("No discovery checkpoint batches were produced")
    leaderboard = pd.concat((pd.read_csv(path) for path in sorted(batch_paths)), ignore_index=True)
    leaderboard = leaderboard.sort_values(["cagr", "log_total_return"], ascending=False).reset_index(drop=True)
    eval_elapsed = time.perf_counter() - eval_t0
    leaderboard.to_csv(output_dir / "leaderboard.csv", index=False)
    leaderboard.to_parquet(output_dir / "leaderboard.parquet", index=False)
    selected_df = leaderboard.drop_duplicates(["formula_id", "top_n"]).head(keep_trades_for).copy()
    trades = _build_selected_trade_ledger(
        df=df,
        gross_ret_np=gross_ret_np,
        x=x,
        weights=weights,
        group_index=group_index,
        group_valid=group_valid,
        selected_df=selected_df,
        device=device,
    )
    trades.to_parquet(output_dir / "discovery_trades.parquet", index=False)
    metadata = {
        "rows": int(len(df)),
        "data_fingerprint": data_fingerprint,
        "run_fingerprint": run_fingerprint,
        "features": features,
        "cost_bps_per_side_grid": cost_grid,
        "device": str(device),
        "torch": torch.__version__,
        "cuda_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
        "load_elapsed_seconds": round(load_elapsed, 3),
        "eval_elapsed_seconds": round(eval_elapsed, 3),
        "total_elapsed_seconds": round(time.perf_counter() - t0, 3),
        "formulas": formulas,
        "batch_size": batch_size,
        "candidate_rows": int(len(leaderboard)),
        "checkpoint_batches": len(batch_paths),
        "checkpoint_dir": str(checkpoint_dir),
        "progress_log": str(progress_path),
        "formulas_per_second": round(formulas / eval_elapsed, 3) if eval_elapsed > 0 else None,
        "candidate_rows_per_second": round(len(leaderboard) / eval_elapsed, 3) if eval_elapsed > 0 else None,
        "cuda_memory_peak_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 3) if device.type == "cuda" else 0.0,
        "config": config,
        "execution_semantics": {
            "signal": "completed_bar_only",
            "entry": "first_actionable_bar_open_after_signal_available",
            "exit": f"close_after_{holding_bars}_held_bars",
            "raw_signal_metrics": "diagnostic_only_not_promotable",
        },
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cost_summary = summarize_cost_sensitivity(leaderboard)
    cost_summary.to_csv(output_dir / "cost_sensitivity.csv", index=False)
    write_report(output_dir, leaderboard, metadata)
    log(f"done discovery candidate_rows={len(leaderboard)} elapsed_sec={metadata['total_elapsed_seconds']}")
    return DiscoveryResult(leaderboard=leaderboard, trades=trades, metadata=metadata)


def _group_offsets(values: np.ndarray) -> list[tuple[int, int]]:
    offsets: list[tuple[int, int]] = []
    start = 0
    for i in range(1, len(values) + 1):
        if i == len(values) or values[i] != values[start]:
            offsets.append((start, i))
            start = i
    return offsets


def _checkpoint_valid(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        cols = set(pd.read_csv(path, nrows=0).columns)
    except Exception:
        return False
    required = {"candidate_id", "base_candidate_id", "formula_id", "top_n", "cost_bps_per_side", "cagr", "log_total_return"}
    return required.issubset(cols)


def _select_top_by_group(scores: torch.Tensor, group_offsets: list[tuple[int, int]], top_n: int) -> torch.Tensor:
    picks = []
    for start, stop in group_offsets:
        width = stop - start
        k = min(top_n, width)
        if k <= 0:
            continue
        local = torch.topk(scores[start:stop], k=k).indices + start
        picks.append(local)
    return torch.cat(picks) if picks else torch.empty(0, dtype=torch.long, device=scores.device)


def _build_group_index(
    group_offsets: list[tuple[int, int]],
    n_rows: int,
    device: torch.device,
) -> tuple[torch.Tensor, torch.Tensor]:
    n_groups = len(group_offsets)
    max_width = max((stop - start for start, stop in group_offsets), default=0)
    index_np = np.zeros((n_groups, max_width), dtype=np.int64)
    valid_np = np.zeros((n_groups, max_width), dtype=bool)
    for group_i, (start, stop) in enumerate(group_offsets):
        width = stop - start
        if width:
            index_np[group_i, :width] = np.arange(start, stop, dtype=np.int64)
            valid_np[group_i, :width] = True
    return torch.as_tensor(index_np, device=device), torch.as_tensor(valid_np, device=device)


def _top_return_matrices_dense(
    scores: torch.Tensor,
    gross_rets: torch.Tensor,
    group_index: torch.Tensor,
    group_valid: torch.Tensor,
    top_ns: list[int],
) -> dict[int, torch.Tensor]:
    max_top_n = max(top_ns)
    padded_scores = scores[group_index]
    padded_scores = padded_scores.masked_fill(~group_valid.unsqueeze(-1), -torch.inf)
    k = min(max_top_n, padded_scores.shape[1])
    local_idx = torch.topk(padded_scores, k=k, dim=1).indices
    expanded_index = group_index.unsqueeze(-1).expand(-1, -1, scores.shape[1])
    picked_flat_idx = torch.gather(expanded_index, dim=1, index=local_idx)
    picked_returns = gross_rets[picked_flat_idx]
    out: dict[int, torch.Tensor] = {}
    for top_n in top_ns:
        take = min(top_n, k)
        out[top_n] = picked_returns[:, :take, :].reshape(-1, scores.shape[1])
    return out


def _metric_rows_for_batch(
    selected_gross_ret: torch.Tensor,
    formula_ids: list[int],
    top_n: int,
    holding_bars: int,
    annualization_years: float,
    cost_grid: list[float],
    family: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    n_trades = selected_gross_ret.shape[0]
    years = max(float(annualization_years), 1.0 / 365.25)
    for cost_bps in cost_grid:
        net = selected_gross_ret - float(cost_bps / 10000.0 * 2.0)
        ruined = torch.any(net <= -0.999999, dim=0)
        safe_net = torch.clamp(net, min=-0.999999)
        log_ret = torch.log1p(safe_net)
        cum_log = torch.cumsum(log_ret, dim=0)
        log_total_return = cum_log[-1, :]
        total_return = torch.expm1(torch.clamp(log_total_return, max=80.0))
        peak_log = torch.cummax(cum_log, dim=0).values
        max_drawdown = torch.min(torch.expm1(cum_log - peak_log), dim=0).values
        cagr = torch.expm1(torch.clamp(log_total_return / years, min=-80.0, max=80.0))
        cagr = torch.where(ruined, torch.full_like(cagr, -1.0), cagr)
        total_return = torch.where(ruined, torch.full_like(total_return, -1.0), total_return)
        win_rate = torch.mean((net > 0).to(torch.float32), dim=0)
        avg_return = torch.mean(net, dim=0)
        worst_return = torch.min(net, dim=0).values
        cols = {
            "log_total_return": log_total_return.detach().cpu().numpy(),
            "total_return": total_return.detach().cpu().numpy(),
            "cagr": cagr.detach().cpu().numpy(),
            "max_drawdown": max_drawdown.detach().cpu().numpy(),
            "win_rate": win_rate.detach().cpu().numpy(),
            "avg_return": avg_return.detach().cpu().numpy(),
            "worst_return": worst_return.detach().cpu().numpy(),
        }
        for local_idx, formula_id in enumerate(formula_ids):
            rows.append(
                {
                    "candidate_id": f"f{formula_id}_top{top_n}_h{holding_bars}_c{_cost_label(cost_bps)}",
                    "base_candidate_id": f"f{formula_id}_top{top_n}_h{holding_bars}",
                    "formula_id": formula_id,
                    "family": family,
                    "top_n": top_n,
                    "holding_bars": holding_bars,
                    "horizon": holding_bars,
                    "cost_bps_per_side": cost_bps,
                    "trades": float(n_trades),
                    "raw_signal_log_total_return": float(cols["log_total_return"][local_idx]),
                    "raw_signal_total_return": float(cols["total_return"][local_idx]),
                    "raw_signal_cagr": float(cols["cagr"][local_idx]),
                    # Compatibility aliases.  Stage 3 treats these as a
                    # diagnostic ranking signal, never a promotion metric.
                    "log_total_return": float(cols["log_total_return"][local_idx]),
                    "total_return": float(cols["total_return"][local_idx]),
                    "cagr": float(cols["cagr"][local_idx]),
                    "max_drawdown": float(cols["max_drawdown"][local_idx]),
                    "win_rate": float(cols["win_rate"][local_idx]),
                    "avg_return": float(cols["avg_return"][local_idx]),
                    "worst_return": float(cols["worst_return"][local_idx]),
                }
            )
    return rows


def _build_selected_trade_ledger(
    df: pd.DataFrame,
    gross_ret_np: np.ndarray,
    x: torch.Tensor,
    weights: np.ndarray,
    group_index: torch.Tensor,
    group_valid: torch.Tensor,
    selected_df: pd.DataFrame,
    device: torch.device,
) -> pd.DataFrame:
    if selected_df.empty:
        return pd.DataFrame()
    formula_ids = selected_df["formula_id"].astype(int).drop_duplicates().tolist()
    max_top_n = int(selected_df["top_n"].max())
    parts: list[pd.DataFrame] = []
    # The discovery tensor can evaluate thousands of formulas efficiently,
    # but rebuilding a ledger for the selected candidates is a different
    # memory shape: rows x selected_formulas.  Keep this bounded so ledger
    # finalization cannot request an entire multi-gigabyte score matrix.
    ledger_formula_chunk = 16
    group_index_cpu = group_index.detach().cpu().numpy()
    group_valid_cpu = group_valid.detach().cpu().numpy()
    for formula_start in range(0, len(formula_ids), ledger_formula_chunk):
        chunk_ids = formula_ids[formula_start : formula_start + ledger_formula_chunk]
        formula_pos = {fid: i for i, fid in enumerate(chunk_ids)}
        w = torch.as_tensor(weights[chunk_ids].T, device=device)
        scores = x @ w
        padded_scores = scores[group_index]
        padded_scores = padded_scores.masked_fill(~group_valid.unsqueeze(-1), -torch.inf)
        k = min(max_top_n, padded_scores.shape[1])
        local_idx = torch.topk(padded_scores, k=k, dim=1).indices
        expanded_index = group_index.unsqueeze(-1).expand(-1, -1, scores.shape[1])
        picked_flat_idx = torch.gather(expanded_index, dim=1, index=local_idx).detach().cpu().numpy()
        for row in selected_df[selected_df["formula_id"].isin(chunk_ids)].itertuples(index=False):
            formula_id = int(row.formula_id)
            top_n = int(row.top_n)
            col = formula_pos[formula_id]
            idx = picked_flat_idx[:, : min(top_n, k), col].reshape(-1)
            part = df.iloc[idx][
                [
                    "symbol",
                    "signal_ts",
                    "signal_available_ts",
                    "entry_ts",
                    "exit_ts",
                    "entry_open",
                    "exit_close",
                    "open",
                    "high",
                    "low",
                    "close",
                ]
            ].copy()
            gross = gross_ret_np[idx]
            cost_bps = float(row.cost_bps_per_side)
            part["candidate_id"] = str(row.base_candidate_id)
            part["timestamp"] = part["signal_ts"]
            part["side"] = "long"
            part["entry_submit_ts"] = part["signal_available_ts"]
            part["entry_ref_price"] = part["entry_open"]
            part["exit_ref_price"] = part["exit_close"]
            part["source_return"] = gross - np.float32(cost_bps / 10000.0 * 2.0)
            part["gross_source_return"] = gross
            part["discovery_cost_bps_per_side"] = cost_bps
            part["rank_formula_id"] = formula_id
            part["top_n"] = top_n
            parts.append(part)
        del picked_flat_idx, local_idx, padded_scores, scores, w
        if device.type == "cuda":
            torch.cuda.empty_cache()
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _annualization_years(timestamps: pd.Series) -> float:
    series = pd.to_datetime(timestamps, utc=True, errors="coerce").dropna()
    if len(series) < 2:
        return 1.0 / 365.25
    return max((series.max() - series.min()).total_seconds() / (365.25 * 24 * 60 * 60), 1.0 / 365.25)


def _top_return_matrices_by_group(
    scores: torch.Tensor,
    gross_rets: torch.Tensor,
    group_offsets: list[tuple[int, int]],
    top_ns: list[int],
) -> dict[int, np.ndarray]:
    max_top_n = max(top_ns)
    parts: dict[int, list[torch.Tensor]] = {top_n: [] for top_n in top_ns}
    for start, stop in group_offsets:
        width = stop - start
        if width <= 0:
            continue
        k = min(max_top_n, width)
        local_idx = torch.topk(scores[start:stop, :], k=k, dim=0).indices + start
        group_returns = gross_rets[local_idx]
        for top_n in top_ns:
            take = min(top_n, k)
            if take > 0:
                parts[top_n].append(group_returns[:take, :])
    out: dict[int, np.ndarray] = {}
    for top_n, tensors in parts.items():
        if tensors:
            out[top_n] = torch.cat(tensors, dim=0).detach().cpu().numpy()
        else:
            out[top_n] = np.empty((0, scores.shape[1]), dtype=np.float32)
    return out


def _benchmark_and_select_batch_size(
    scan: dict[str, Any],
    output_dir: Path,
    x: torch.Tensor,
    gross_rets: torch.Tensor,
    weights: np.ndarray,
    group_index: torch.Tensor,
    group_valid: torch.Tensor,
    top_ns: list[int],
    current_batch_size: int,
    log,
) -> int:
    exec_cfg = scan.get("execution", {})
    requested = exec_cfg.get("benchmark_batch_sizes")
    if requested:
        candidates = [int(x) for x in requested]
    else:
        candidates = sorted(set(max(8, int(current_batch_size * factor)) for factor in [0.5, 1.0, 1.5, 2.0]))
    min_util = float(exec_cfg.get("min_gpu_utilization_pct", 35.0))
    results = []
    for batch in candidates:
        if batch > len(weights):
            continue
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        sampler = NvidiaSmiSampler()
        try:
            sampler.start()
            t0 = time.perf_counter()
            w = torch.as_tensor(weights[:batch].T, device=x.device)
            scores = x @ w
            mats = _top_return_matrices_dense(scores, gross_rets, group_index, group_valid, top_ns)
            forced = 0.0
            formula_ids = list(range(batch))
            for top_n, mat in mats.items():
                rows = _metric_rows_for_batch(
                    selected_gross_ret=mat,
                    formula_ids=formula_ids,
                    top_n=top_n,
                    holding_bars=int(scan.get("holding_bars", scan.get("horizon", 1))),
                    annualization_years=1.0,
                    cost_grid=[float(x) for x in scan.get("cost_bps_per_side_grid", [scan.get("cost_bps_per_side", 5.0)])],
                    family=str(scan.get("family", "benchmark")),
                )
                forced += len(rows)
            if x.device.type == "cuda":
                torch.cuda.synchronize()
            elapsed = max(time.perf_counter() - t0, 1e-9)
            samples = sampler.stop()
            peak_gb = torch.cuda.max_memory_allocated() / 1024**3
            result = {
                "batch_size": batch,
                "elapsed_seconds": elapsed,
                "formulas_per_second": batch / elapsed,
                "gpu_util_avg": _avg([s["gpu_util"] for s in samples]),
                "gpu_power_avg_w": _avg([s["power_w"] for s in samples]),
                "gpu_mem_peak_gb": peak_gb,
            }
            results.append(result)
            log(
                "benchmark "
                f"batch_size={batch} formulas_sec={result['formulas_per_second']:.2f} "
                f"gpu_util_avg={result['gpu_util_avg']:.1f} "
                f"power_w_avg={result['gpu_power_avg_w']:.1f} "
                f"gpu_mem_peak_gb={peak_gb:.3f}"
            )
        except torch.OutOfMemoryError:
            sampler.stop()
            log(f"benchmark batch_size={batch} oom")
            torch.cuda.empty_cache()
            continue
    if not results:
        raise RuntimeError("CUDA benchmark could not find a runnable batch size")
    best = max(results, key=lambda r: r["formulas_per_second"])
    (output_dir / "benchmark_results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    if best["gpu_util_avg"] < min_util:
        raise RuntimeError(
            f"CUDA benchmark underutilized GPU: best batch_size={best['batch_size']} "
            f"avg_util={best['gpu_util_avg']:.1f}% < {min_util:.1f}%. "
            "Refusing full run."
        )
    log(f"benchmark selected_batch_size={best['batch_size']} formulas_sec={best['formulas_per_second']:.2f}")
    return int(best["batch_size"])


class NvidiaSmiSampler:
    def __init__(self, interval_seconds: float = 0.25) -> None:
        self.interval_seconds = interval_seconds
        self.samples: list[dict[str, float]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> list[dict[str, float]]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        return self.samples

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                out = subprocess.check_output(
                    [
                        "nvidia-smi",
                        "--query-gpu=utilization.gpu,power.draw",
                        "--format=csv,noheader,nounits",
                    ],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                ).strip()
                if out:
                    first = out.splitlines()[0]
                    util, power = _parse_nvidia_smi_sample(first)
                    self.samples.append({"gpu_util": util, "power_w": power})
            except Exception:
                pass
            self._stop.wait(self.interval_seconds)


def _parse_nvidia_smi_sample(line: str) -> tuple[float, float]:
    fields = [x.strip() for x in line.split(",")]
    if not fields or fields[0] in {"", "N/A", "[N/A]"}:
        raise ValueError(f"Missing GPU utilization in nvidia-smi sample: {line!r}")
    util = float(fields[0])
    power_raw = fields[1] if len(fields) > 1 else "N/A"
    power = float("nan") if power_raw in {"", "N/A", "[N/A]"} else float(power_raw)
    return util, power


def _avg(values: list[float]) -> float:
    vals = [v for v in values if np.isfinite(v)]
    return float(np.mean(vals)) if vals else 0.0


def _cost_label(cost_bps: float) -> str:
    return str(cost_bps).replace(".", "p").replace("-", "m")


def _timeframe_minutes(value: str) -> int:
    match = re.fullmatch(r"(\d+)(m|h|d)", value.strip().lower())
    if not match:
        raise ValueError(f"Unsupported timeframe for exit_ts calculation: {value!r}")
    n = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return n
    if unit == "h":
        return n * 60
    if unit == "d":
        return n * 24 * 60
    raise AssertionError(unit)


def summarize_cost_sensitivity(leaderboard: pd.DataFrame) -> pd.DataFrame:
    if leaderboard.empty:
        return leaderboard.copy()

    ordered = leaderboard.sort_values(["base_candidate_id", "cost_bps_per_side"]).reset_index(drop=True)
    group = ordered.groupby("base_candidate_id", sort=False)
    first = group.first().reset_index()
    last = group.last().reset_index()
    best_idx = group["total_return"].idxmax()
    best = ordered.loc[best_idx].set_index("base_candidate_id")
    profitable = ordered["cost_bps_per_side"].where(ordered["total_return"] > 0, -1.0)
    prof = profitable.groupby(ordered["base_candidate_id"], sort=False).max()
    agg = group.agg(
        best_log_total_return=("log_total_return", "max"),
        best_total_return=("total_return", "max"),
        best_cagr=("cagr", "max"),
        worst_drawdown_across_costs=("max_drawdown", "min"),
    ).reset_index()

    summary = first[
        ["base_candidate_id", "family", "formula_id", "top_n", "horizon", "trades"]
    ].merge(agg, on="base_candidate_id", how="left")
    summary["best_cost_bps_per_side"] = summary["base_candidate_id"].map(best["cost_bps_per_side"])
    summary["max_profitable_cost_bps_per_side"] = summary["base_candidate_id"].map(prof).fillna(-1.0)
    summary["log_return_at_lowest_cost"] = first["log_total_return"].to_numpy()
    summary["log_return_at_highest_cost"] = last["log_total_return"].to_numpy()
    summary["return_at_lowest_cost"] = first["total_return"].to_numpy()
    summary["return_at_highest_cost"] = last["total_return"].to_numpy()
    summary["cagr_at_highest_cost"] = last["cagr"].to_numpy()

    return summary.sort_values(
        ["max_profitable_cost_bps_per_side", "return_at_highest_cost", "best_total_return"],
        ascending=False,
    )


def write_report(output_dir: Path, leaderboard: pd.DataFrame, metadata: dict[str, Any]) -> None:
    lines = [
        "# Discovery Report",
        "",
        f"Rows evaluated: {metadata['rows']}",
        f"Device: {metadata['device']}",
        f"CUDA: {metadata['cuda_name'] or 'not used'}",
        f"Cost grid bps/side: {metadata['cost_bps_per_side_grid']}",
        f"Eval elapsed seconds: {metadata['eval_elapsed_seconds']}",
        f"Formulas/sec: {metadata['formulas_per_second']}",
        f"Candidate rows/sec: {metadata['candidate_rows_per_second']}",
        f"CUDA memory peak GB: {metadata['cuda_memory_peak_gb']}",
        "",
        "## Top Candidates",
        "",
    ]
    cols = ["candidate_id", "family", "cagr", "log_total_return", "total_return", "max_drawdown", "win_rate", "trades"]
    lines.append("```text")
    lines.append(leaderboard[cols].head(25).to_string(index=False))
    lines.append("```")
    lines.append("")
    (output_dir / "discovery_report.md").write_text("\n".join(lines), encoding="utf-8")
