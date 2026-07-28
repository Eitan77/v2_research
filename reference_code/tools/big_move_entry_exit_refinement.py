"""Frozen entry/exit refinement for the volatility-first SOXL/SOXS leaders."""
from __future__ import annotations

import argparse
import itertools
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

import big_move_volatility_direction_cuda as base


CORES = (
    {"core": "A_90m_qqq_smh", "checkpoint": 90, "model": "qqq_smh", "score_bps": 100, "range_bps": 100, "rvol": 1.25},
    {"core": "B_90m_semis", "checkpoint": 90, "model": "semis_blend", "score_bps": 50, "range_bps": 100, "rvol": 1.25},
    {"core": "C_15m_smh", "checkpoint": 15, "model": "smh", "score_bps": 50, "range_bps": 300, "rvol": 1.25},
)
ENTRY_SPECS = (
    [{"entry_model": "market_next_open", "offset_bps": 0, "wait_min": 0}]
    + [
        {"entry_model": "pullback_limit", "offset_bps": off, "wait_min": wait}
        for off, wait in itertools.product((25, 50, 100, 200), (15, 30))
    ]
    + [
        {"entry_model": "breakout_stop", "offset_bps": off, "wait_min": wait}
        for off, wait in itertools.product((10, 25, 50), (15, 30))
    ]
)
EXIT_SPECS = (
    [{"horizon": h, "tp_bps": 0.0, "sl_bps": 0.0} for h in (30, 60, 120, "close")]
    + [
        {"horizon": h, "tp_bps": tp, "sl_bps": sl}
        for h, tp, sl in itertools.product((60, 120, "close"), (100, 200, 300, 500), (50, 100, 200))
    ]
)


def core_state(a: dict, core: dict) -> tuple[np.ndarray, np.ndarray]:
    cp = core["checkpoint"]
    signal_i = cp // 5 - 1
    idx = a["symbol_to_i"]
    score = base.direction_score(a, core["model"], signal_i)
    pair_range = np.maximum(
        a["cum_high"][idx["SOXL"], :, signal_i] / a["cum_low"][idx["SOXL"], :, signal_i] - 1,
        a["cum_high"][idx["SOXS"], :, signal_i] / a["cum_low"][idx["SOXS"], :, signal_i] - 1,
    )
    pair_rvol = np.nanmean(np.stack([
        a["cum_volume"][idx["SOXL"], :, signal_i] / a["hist_cum_volume"][idx["SOXL"], :, signal_i],
        a["cum_volume"][idx["SOXS"], :, signal_i] / a["hist_cum_volume"][idx["SOXS"], :, signal_i],
    ]), axis=0)
    mask = (
        np.isfinite(score) & (score != 0)
        & (np.abs(score) * 10000 >= core["score_bps"])
        & (pair_range * 10000 >= core["range_bps"])
        & (pair_rvol >= core["rvol"])
    )
    return score, mask


def entry_paths(a: dict, score: np.ndarray, checkpoint: int, spec: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    idx = a["symbol_to_i"]
    positive = score > 0
    selected = np.where(positive, idx["SOXL"], idx["SOXS"])
    n, bars = len(a["dates"]), 78
    signal_i = checkpoint // 5 - 1
    next_i = checkpoint // 5
    fill_price = np.full(n, np.nan, np.float32)
    fill_i = np.full(n, -1, np.int16)
    model = spec["entry_model"]
    for d in range(n):
        si = int(selected[d])
        if model == "market_next_open":
            px = a["open"][si, d, next_i]
            if np.isfinite(px) and px > 0:
                fill_price[d], fill_i[d] = px, next_i
            continue
        wait_bars = int(spec["wait_min"] // 5)
        if model == "pullback_limit":
            anchor = a["close"][si, d, signal_i]
            trigger = anchor * (1 - spec["offset_bps"] / 10000)
            for bi in range(next_i, min(next_i + wait_bars, bars)):
                if np.isfinite(a["low"][si, d, bi]) and a["low"][si, d, bi] <= trigger:
                    fill_price[d], fill_i[d] = trigger, bi
                    break
        else:
            anchor = a["high"][si, d, signal_i]
            trigger = anchor * (1 + spec["offset_bps"] / 10000)
            for bi in range(next_i, min(next_i + wait_bars, bars)):
                if np.isfinite(a["high"][si, d, bi]) and a["high"][si, d, bi] >= trigger:
                    op = a["open"][si, d, bi]
                    fill_price[d] = max(trigger, op) if np.isfinite(op) else trigger
                    fill_i[d] = bi
                    break
    max_path = bars - next_i
    hi = np.full((n, max_path), np.nan, np.float32)
    lo = np.full_like(hi, np.nan)
    cl = np.full_like(hi, np.nan)
    for d in range(n):
        if fill_i[d] < 0:
            continue
        si = int(selected[d])
        # Market entries are exposed to their entry bar. Conditional entries
        # begin risk accounting on the following bar, avoiding optimistic
        # assumptions about ordering inside the fill bar.
        start = int(fill_i[d] if model == "market_next_open" else fill_i[d] + 1)
        if start >= bars:
            continue
        length = bars - start
        hi[d, :length] = a["high"][si, d, start:]
        lo[d, :length] = a["low"][si, d, start:]
        cl[d, :length] = a["close"][si, d, start:]
    return fill_price, fill_i, hi, lo, cl


def run(a: dict, out: Path) -> pd.DataFrame:
    dates = a["dates"]
    train = dates <= base.TRAIN_END
    valid = dates >= base.VALID_START
    rows = []
    for core in CORES:
        score, core_mask = core_state(a, core)
        for entry_id, entry_spec in enumerate(ENTRY_SPECS):
            entry, fill_i, hi, lo, cl = entry_paths(a, score, core["checkpoint"], entry_spec)
            returns = base.path_returns_gpu(entry, hi, lo, cl, EXIT_SPECS)
            filled = core_mask & np.isfinite(entry)
            selections = int(core_mask.sum())
            for exit_id, exit_spec in enumerate(EXIT_SPECS):
                gross = returns[:, exit_id]
                active = filled & np.isfinite(gross)
                if active.sum() < 40:
                    continue
                for cost in base.COSTS:
                    daily = np.zeros(len(dates), np.float64)
                    daily[active] = gross[active] - 2 * cost / 10000
                    record = {
                        **core, "entry_id": entry_id, **entry_spec,
                        "exit_id": exit_id, **exit_spec, "cost_bps_side": cost,
                        "selections": selections, "fills": int(active.sum()),
                        "fill_rate": float(active.sum() / selections) if selections else 0,
                        "trades_per_week": float(active.sum() / len(dates) * 5),
                        "mean_gross_trade": float(np.mean(gross[active])),
                    }
                    record.update({f"full_{k}": v for k, v in base.metrics(dates, daily).items()})
                    record.update({f"train_{k}": v for k, v in base.metrics(dates[train], daily[train]).items()})
                    record.update({f"valid_{k}": v for k, v in base.metrics(dates[valid], daily[valid]).items()})
                    rows.append(record)
    result = pd.DataFrame(rows)
    result["robust_gate"] = (
        result.cost_bps_side.eq(5)
        & result.train_cagr.gt(0) & result.valid_cagr.gt(0)
        & result.full_max_drawdown.gt(-.15)
        & result.full_dd_duration_calendar_days.le(92)
        & result.full_worst_month.gt(-.08)
        & result.full_positive_years.ge(result.full_years - 1)
        & result.trades_per_week.ge(.5)
        & result.fill_rate.ge(.5)
    )
    result.to_csv(out / "entry_exit_refinement_grid.csv", index=False)
    cost5 = result[result.cost_bps_side.eq(5)].copy()
    cost5["train_score"] = (
        cost5.train_cagr + .5 * cost5.train_sharpe
        + cost5.train_max_drawdown + .25 * cost5.train_worst_month
    )
    cost5.sort_values("train_score", ascending=False).head(50).to_csv(out / "training_frozen_top50.csv", index=False)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="D:/AlgoResearch/data/catalog.duckdb")
    ap.add_argument("--bars-cache", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA required")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    bars = pd.read_parquet(args.bars_cache)
    a = base.build_arrays(bars)
    result = run(a, out)
    frozen = pd.read_csv(out / "training_frozen_top50.csv")
    best = frozen.sort_values("valid_cagr", ascending=False).iloc[0]
    meta = {
        "cores": len(CORES), "entry_specs": len(ENTRY_SPECS), "exit_specs": len(EXIT_SPECS),
        "grid_rows": len(result), "robust_gate_rows": int(result.robust_gate.sum()),
        "holdout_access": False, "device": torch.cuda.get_device_name(0),
        "gpu_peak_memory_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 3),
        "elapsed_sec": round(time.perf_counter() - t0, 2),
    }
    (out / "run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    report = f"""# Frozen big-move entry/exit refinement

**Verdict: {'RESEARCH CANDIDATE' if meta['robust_gate_rows'] else 'NO CANDIDATE'}.**

- Three training-selected signal neighborhoods were frozen before this refinement.
- Tested {meta['entry_specs']} entries: next-bar market, pullback limits, and breakout stops with bounded waits.
- Tested {meta['exit_specs']} timed or full-path TP/SL exits. Stops win same-bar collisions.
- Conditional fills begin risk accounting on the bar after the fill bar; no passive rebate or spread improvement is credited.
- {meta['grid_rows']:,} costed cells; {meta['robust_gate_rows']} passed the complete 5 bp/side gate. The 2026 holdout was not accessed.

Best training-frozen row by validation CAGR: core `{best['core']}`, entry `{best['entry_model']}` offset {best['offset_bps']:.0f} bp / wait {best['wait_min']:.0f}m, exit `{best['horizon']}` TP {best['tp_bps']:.0f} / SL {best['sl_bps']:.0f} bp. At 5 bp/side: train CAGR {best['train_cagr']:.1%}, validation CAGR {best['valid_cagr']:.1%}, full CAGR {best['full_cagr']:.1%}, max drawdown {best['full_max_drawdown']:.1%}, worst month {best['full_worst_month']:.1%}, recovery {int(best['full_dd_duration_calendar_days'])} days, fill rate {best['fill_rate']:.1%}, {best['trades_per_week']:.2f} trades/week.
"""
    (out / "REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
