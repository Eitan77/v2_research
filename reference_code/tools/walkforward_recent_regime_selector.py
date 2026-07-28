"""Causal monthly selector for a small library of big-move strategies."""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

import big_move_volatility_direction_cuda as big
import cross_pair_big_move_cuda as cross


CHECKPOINTS = (15, 30, 60, 90)
SELECTORS = ("semi_only", "tech_only", "largest_early_range")
GATES = (
    {"gate": "rvol_1p25", "score_bps": 0, "range_bps": 0, "rvol": 1.25},
    {"gate": "strong_trend", "score_bps": 50, "range_bps": 300, "rvol": 1.25},
)
EXITS = (
    {"horizon": 15, "tp_bps": 0.0, "sl_bps": 0.0},
    {"horizon": 60, "tp_bps": 0.0, "sl_bps": 0.0},
    {"horizon": "close", "tp_bps": 0.0, "sl_bps": 0.0},
)
LOOKBACKS = (6, 12, 24)
OBJECTIVES = ("sharpe", "return_dd")
COST_BPS_SIDE = 5.0


def library(a: dict) -> tuple[pd.DataFrame, np.ndarray]:
    dates = a["dates"]
    specs = []
    columns = []
    exit_lookup = {
        (str(x["horizon"]), x["tp_bps"], x["sl_bps"]): i
        for i, x in enumerate(big.exit_specs())
    }
    for cp, selector in itertools.product(CHECKPOINTS, SELECTORS):
        st = cross.state(a, cp, selector)
        entry, hi, lo, cl = cross.selected_paths(a, st["selected"], cp)
        returns = big.path_returns_gpu(entry, hi, lo, cl, big.exit_specs())
        for gate, exit_spec in itertools.product(GATES, EXITS):
            active = (
                np.isfinite(st["score"]) & (st["score"] != 0)
                & (np.abs(st["score"]) * 10000 >= gate["score_bps"])
                & (st["range"] * 10000 >= gate["range_bps"])
                & (st["rvol"] >= gate["rvol"])
            )
            exit_id = exit_lookup[(str(exit_spec["horizon"]), exit_spec["tp_bps"], exit_spec["sl_bps"])]
            gross = returns[:, exit_id]
            active &= np.isfinite(gross)
            daily = np.zeros(len(dates), np.float32)
            daily[active] = gross[active] - 2 * COST_BPS_SIDE / 10000
            specs.append({
                "candidate": len(specs), "checkpoint": cp, "selector": selector,
                **gate, **exit_spec, "trades": int(active.sum()),
            })
            columns.append(daily)
    return pd.DataFrame(specs), np.stack(columns, axis=1)


def dd(returns: np.ndarray) -> float:
    eq = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(np.r_[1.0, eq])[:-1]
    return float(np.min(eq / peak - 1))


def select_monthly(dates: np.ndarray, specs: pd.DataFrame, matrix: np.ndarray, lookback: int, objective: str) -> tuple[np.ndarray, pd.DataFrame]:
    months = dates.astype("datetime64[M]")
    unique_months = np.unique(months)
    combined = np.zeros(len(dates), np.float64)
    decisions = []
    for month in unique_months:
        if month < np.datetime64("2021-01"):
            continue
        start = month - np.timedelta64(lookback, "M")
        history = (months >= start) & (months < month)
        test = months == month
        if history.sum() < 80 or not test.any():
            continue
        h = matrix[history]
        trade_counts = (h != 0).sum(axis=0)
        mean = h.mean(axis=0)
        std = h.std(axis=0, ddof=1)
        sharpe = np.divide(mean, std, out=np.full_like(mean, -np.inf), where=std > 0) * np.sqrt(252)
        total = np.prod(1 + h, axis=0) - 1
        dds = np.array([dd(h[:, i]) for i in range(h.shape[1])])
        score = sharpe if objective == "sharpe" else total + dds
        score[trade_counts < max(6, lookback)] = -np.inf
        chosen = int(np.argmax(score))
        if not np.isfinite(score[chosen]) or total[chosen] <= 0:
            decisions.append({"month": str(month), "candidate": -1, "reason": "no_positive_history"})
            continue
        combined[test] = matrix[test, chosen]
        decisions.append({
            "month": str(month), "candidate": chosen, "history_trades": int(trade_counts[chosen]),
            "history_total": float(total[chosen]), "history_sharpe": float(sharpe[chosen]),
            "history_drawdown": float(dds[chosen]), "objective_score": float(score[chosen]),
            **specs.iloc[chosen].to_dict(),
        })
    return combined, pd.DataFrame(decisions)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars-cache", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA required")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    big.SYMBOLS = cross.SYMBOLS
    a = big.build_arrays(pd.read_parquet(args.bars_cache))
    specs, matrix = library(a)
    specs.to_csv(out / "candidate_library.csv", index=False)
    np.save(out / "daily_return_matrix_5bps.npy", matrix)
    rows = []
    all_decisions = []
    for lookback, objective in itertools.product(LOOKBACKS, OBJECTIVES):
        returns, decisions = select_monthly(a["dates"], specs, matrix, lookback, objective)
        for label, mask in (
            ("walkforward_2021_2025", a["dates"] >= np.datetime64("2021-01-01")),
            ("recent_2024_2025", a["dates"] >= np.datetime64("2024-01-01")),
            ("recent_2025", a["dates"] >= np.datetime64("2025-01-01")),
        ):
            m = big.metrics(a["dates"][mask], returns[mask])
            rows.append({
                "lookback_months": lookback, "objective": objective, "period": label,
                "trades": int((returns[mask] != 0).sum()),
                "total_return": float(np.prod(1 + returns[mask]) - 1),
                **m,
            })
        decisions["lookback_months"] = lookback
        decisions["objective"] = objective
        all_decisions.append(decisions)
    result = pd.DataFrame(rows)
    result["recent_gate"] = (
        result.period.eq("recent_2025")
        & result.total_return.ge(.15) & result.max_drawdown.ge(-.15)
        & result.worst_month.ge(-.08) & result.trades.ge(20)
        & result.positive_month_fraction.ge(.60)
    )
    result.to_csv(out / "walkforward_results.csv", index=False)
    pd.concat(all_decisions, ignore_index=True).to_csv(out / "monthly_decisions.csv", index=False)
    recent = result[result.period.eq("recent_2025")].sort_values("total_return", ascending=False)
    best = recent.iloc[0]
    meta = {
        "library_candidates": len(specs), "meta_rules": len(LOOKBACKS) * len(OBJECTIVES),
        "recent_gate_passes": int(result.recent_gate.sum()), "holdout_access": False,
        "device": torch.cuda.get_device_name(0),
        "gpu_peak_memory_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 3),
    }
    (out / "run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    report = f"""# Walk-forward recent-regime selector

**2025 recent-gate passes: {meta['recent_gate_passes']}.**

- The library contains {meta['library_candidates']} simple strategies fixed before monthly selection.
- Each month selects one rule using only the preceding 6, 12, or 24 months, by either trailing Sharpe or return-minus-drawdown.
- The chosen rule is then frozen for the next month. No 2026 data was accessed.
- Costs are 5 bp per side; entries are next-bar opens; the library includes semiconductor, Nasdaq, and largest-early-range pair selection.

Best 2025 meta-rule: {int(best['lookback_months'])}-month `{best['objective']}` selection. Total return {best['total_return']:.1%}, CAGR {best['cagr']:.1%}, max drawdown {best['max_drawdown']:.1%}, worst month {best['worst_month']:.1%}, recovery {int(best['dd_duration_calendar_days'])} days, {int(best['trades'])} trades, {best['positive_month_fraction']:.1%} positive months.
"""
    (out / "REPORT.md").write_text(report, encoding="utf-8")
    print(result.to_string(index=False))
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
