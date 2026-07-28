"""One-trade-per-day opening-range breakout into leveraged ETF pairs."""
from __future__ import annotations

import argparse
import itertools
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

import big_move_volatility_direction_cuda as big
import cross_pair_big_move_cuda as cross


PAIRS = {
    "semis": {"underlying": "SMH", "bull": "SOXL", "bear": "SOXS"},
    "nasdaq": {"underlying": "QQQ", "bull": "TQQQ", "bear": "SQQQ"},
}
RANGE_MINUTES = (15, 30, 45)
LAST_BREAKOUT_MINUTES = (60, 90, 120)
BUFFERS_BPS = (0, 10, 25)
MIN_RVOL = (0, 1.25, 1.5)
MIN_RANGE_BPS = (0, 25, 50, 100)
TRAIN_END = np.datetime64("2023-12-31")
VALID_START = np.datetime64("2024-01-01")


def signal_and_paths(a: dict, pair: dict, range_min: int, last_min: int, buffer_bps: int) -> dict:
    idx = a["symbol_to_i"]
    ui, bull_i, bear_i = idx[pair["underlying"]], idx[pair["bull"]], idx[pair["bear"]]
    n = len(a["dates"])
    range_bars = range_min // 5
    last_signal_i = last_min // 5 - 1
    rh = np.nanmax(a["high"][ui, :, :range_bars], axis=1)
    rl = np.nanmin(a["low"][ui, :, :range_bars], axis=1)
    range_ret = rh / rl - 1
    up_level = rh * (1 + buffer_bps / 10000)
    dn_level = rl * (1 - buffer_bps / 10000)
    signal_i = np.full(n, -1, np.int16)
    direction = np.zeros(n, np.int8)
    for d in range(n):
        for bi in range(range_bars, min(last_signal_i + 1, 77)):
            close = a["close"][ui, d, bi]
            if not np.isfinite(close):
                continue
            if close > up_level[d]:
                signal_i[d], direction[d] = bi, 1
                break
            if close < dn_level[d]:
                signal_i[d], direction[d] = bi, -1
                break
    selected = np.where(direction > 0, bull_i, bear_i)
    entry = np.full(n, np.nan, np.float32)
    max_path = 78 - range_bars - 1
    hi = np.full((n, max_path), np.nan, np.float32)
    lo = np.full_like(hi, np.nan)
    cl = np.full_like(hi, np.nan)
    for d in range(n):
        if signal_i[d] < 0 or signal_i[d] + 1 >= 78:
            continue
        si = int(selected[d])
        start = int(signal_i[d] + 1)
        entry[d] = a["open"][si, d, start]
        length = 78 - start
        hi[d, :length] = a["high"][si, d, start:]
        lo[d, :length] = a["low"][si, d, start:]
        cl[d, :length] = a["close"][si, d, start:]
    # Opening RVOL is frozen at range completion, before any later breakout.
    ri = range_bars - 1
    pair_rvol = np.nanmean(np.stack([
        a["cum_volume"][bull_i, :, ri] / a["hist_cum_volume"][bull_i, :, ri],
        a["cum_volume"][bear_i, :, ri] / a["hist_cum_volume"][bear_i, :, ri],
    ]), axis=0)
    return {
        "range_ret": range_ret, "rvol": pair_rvol, "signal_i": signal_i,
        "direction": direction, "entry": entry, "hi": hi, "lo": lo, "cl": cl,
    }


def metrics_with_total(dates: np.ndarray, returns: np.ndarray) -> dict:
    return {"total_return": float(np.prod(1 + returns) - 1), **big.metrics(dates, returns)}


def run(a: dict, out: Path) -> pd.DataFrame:
    dates = a["dates"]
    train = dates <= TRAIN_END
    valid = dates >= VALID_START
    recent = dates >= np.datetime64("2025-01-01")
    exits = big.exit_specs()
    rows = []
    for pair_name, range_min, last_min, buffer in itertools.product(
        PAIRS, RANGE_MINUTES, LAST_BREAKOUT_MINUTES, BUFFERS_BPS
    ):
        state = signal_and_paths(a, PAIRS[pair_name], range_min, last_min, buffer)
        ret_matrix = big.path_returns_gpu(state["entry"], state["hi"], state["lo"], state["cl"], exits)
        for rvol, min_range, exit_id in itertools.product(MIN_RVOL, MIN_RANGE_BPS, range(len(exits))):
            exit_spec = exits[exit_id]
            gross = ret_matrix[:, exit_id]
            active = (
                (state["signal_i"] >= 0) & np.isfinite(state["entry"]) & np.isfinite(gross)
                & (state["rvol"] >= rvol) & (state["range_ret"] * 10000 >= min_range)
            )
            if active.sum() < 60:
                continue
            for cost in big.COSTS:
                daily = np.zeros(len(dates), np.float64)
                daily[active] = gross[active] - 2 * cost / 10000
                rec = {
                    "pair": pair_name, "range_min": range_min, "last_breakout_min": last_min,
                    "buffer_bps": buffer, "min_rvol": rvol, "min_range_bps": min_range,
                    "exit_id": exit_id, **exit_spec, "cost_bps_side": cost,
                    "trades": int(active.sum()), "trades_per_week": float(active.sum() / len(dates) * 5),
                    "train_trades": int((active & train).sum()),
                    "valid_trades": int((active & valid).sum()),
                    "recent_trades": int((active & recent).sum()),
                    "mean_gross_trade": float(np.mean(gross[active])),
                }
                rec.update({f"full_{k}": v for k, v in metrics_with_total(dates, daily).items()})
                rec.update({f"train_{k}": v for k, v in metrics_with_total(dates[train], daily[train]).items()})
                rec.update({f"valid_{k}": v for k, v in metrics_with_total(dates[valid], daily[valid]).items()})
                rec.update({f"recent_{k}": v for k, v in metrics_with_total(dates[recent], daily[recent]).items()})
                rows.append(rec)
    result = pd.DataFrame(rows)
    result["recent_gate"] = (
        result.cost_bps_side.eq(5)
        & result.train_cagr.gt(0) & result.valid_cagr.gt(0)
        & result.recent_total_return.ge(.15)
        & result.recent_max_drawdown.ge(-.15)
        & result.recent_worst_month.ge(-.08)
        & result.recent_positive_month_fraction.ge(.60)
        & result.recent_trades.ge(20)
    )
    result.to_csv(out / "opening_range_breakout_grid.csv", index=False)
    cost5 = result[result.cost_bps_side.eq(5)].copy()
    cost5["train_score"] = (
        cost5.train_cagr + .5 * cost5.train_sharpe
        + cost5.train_max_drawdown + .25 * cost5.train_worst_month
    )
    cost5.sort_values("train_score", ascending=False).head(50).to_csv(out / "training_through_2023_top50.csv", index=False)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars-cache", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA required")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    big.SYMBOLS = cross.SYMBOLS
    a = big.build_arrays(pd.read_parquet(args.bars_cache))
    result = run(a, out)
    frozen = pd.read_csv(out / "training_through_2023_top50.csv")
    best = frozen.sort_values("recent_total_return", ascending=False).iloc[0]
    meta = {
        "grid_rows": len(result), "recent_gate_rows": int(result.recent_gate.sum()),
        "selection_end": "2023-12-31", "recent_validation": "2024-01-01 through 2025-12-31",
        "holdout_access": False, "device": torch.cuda.get_device_name(0),
        "gpu_peak_memory_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 3),
        "elapsed_sec": round(time.perf_counter() - t0, 2),
    }
    (out / "run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    report = f"""# Recent opening-range breakout study

**Recent-gate passes: {meta['recent_gate_rows']}.**

- Signal family fixed on data through 2023; 2024–2025 is recent validation; 2026 remained sealed.
- First confirmed close outside the completed SMH or QQQ opening range; enter the corresponding leveraged ETF at the next 5-minute open.
- One trade per day. Full-path TP/SL with stop-first same-bar ambiguity, or timed exit.
- Best training-selected row by 2025 return: `{best['pair']}`, {int(best['range_min'])}m range, breakout allowed through {int(best['last_breakout_min'])}m, buffer {best['buffer_bps']:.0f} bp, RVOL ≥{best['min_rvol']:.2f}, range ≥{best['min_range_bps']:.0f} bp; exit `{best['horizon']}` TP {best['tp_bps']:.0f}/SL {best['sl_bps']:.0f} bp.
- At 5 bp/side: train CAGR {best['train_cagr']:.1%}, 2024–2025 CAGR {best['valid_cagr']:.1%}, 2025 return {best['recent_total_return']:.1%}, 2025 max drawdown {best['recent_max_drawdown']:.1%}, worst month {best['recent_worst_month']:.1%}, {best['recent_positive_month_fraction']:.1%} positive months, {int(best['recent_trades'])} trades.
"""
    (out / "REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
