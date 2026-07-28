"""Cross-pair large-move selector across SOXL/SOXS and TQQQ/SQQQ."""
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


SYMBOLS = ("SOXL", "SOXS", "TQQQ", "SQQQ", "SMH", "QQQ", "SPY", "NVDA", "AMD", "AVGO")
CHECKPOINTS = (15, 30, 60, 90)
SELECTORS = ("semi_only", "tech_only", "largest_early_range", "largest_underlying_move", "largest_score_rvol")


def state(a: dict, checkpoint: int, selector: str) -> dict:
    i = checkpoint // 5 - 1
    idx = a["symbol_to_i"]
    def ret(s: str) -> np.ndarray:
        si = idx[s]
        return a["close"][si, :, i] / a["session_open"][si] - 1
    semi = .7 * ret("SMH") + .1 * ret("NVDA") + .1 * ret("AMD") + .1 * ret("AVGO")
    tech = .8 * ret("QQQ") + .2 * ret("SPY")
    semi_range = np.maximum(
        a["cum_high"][idx["SOXL"], :, i] / a["cum_low"][idx["SOXL"], :, i] - 1,
        a["cum_high"][idx["SOXS"], :, i] / a["cum_low"][idx["SOXS"], :, i] - 1,
    )
    tech_range = np.maximum(
        a["cum_high"][idx["TQQQ"], :, i] / a["cum_low"][idx["TQQQ"], :, i] - 1,
        a["cum_high"][idx["SQQQ"], :, i] / a["cum_low"][idx["SQQQ"], :, i] - 1,
    )
    semi_rvol = np.nanmean(np.stack([
        a["cum_volume"][idx["SOXL"], :, i] / a["hist_cum_volume"][idx["SOXL"], :, i],
        a["cum_volume"][idx["SOXS"], :, i] / a["hist_cum_volume"][idx["SOXS"], :, i],
    ]), axis=0)
    tech_rvol = np.nanmean(np.stack([
        a["cum_volume"][idx["TQQQ"], :, i] / a["hist_cum_volume"][idx["TQQQ"], :, i],
        a["cum_volume"][idx["SQQQ"], :, i] / a["hist_cum_volume"][idx["SQQQ"], :, i],
    ]), axis=0)
    if selector == "semi_only":
        use_semi = np.ones(len(semi), bool)
    elif selector == "tech_only":
        use_semi = np.zeros(len(semi), bool)
    elif selector == "largest_early_range":
        use_semi = semi_range >= tech_range
    elif selector == "largest_underlying_move":
        use_semi = np.abs(semi) >= np.abs(tech)
    else:
        use_semi = np.abs(semi) * semi_rvol >= np.abs(tech) * tech_rvol
    score = np.where(use_semi, semi, tech)
    pair_range = np.where(use_semi, semi_range, tech_range)
    pair_rvol = np.where(use_semi, semi_rvol, tech_rvol)
    positive = score > 0
    selected = np.where(
        use_semi,
        np.where(positive, idx["SOXL"], idx["SOXS"]),
        np.where(positive, idx["TQQQ"], idx["SQQQ"]),
    )
    return {"score": score, "range": pair_range, "rvol": pair_rvol, "selected": selected, "use_semi": use_semi}


def selected_paths(a: dict, selected: np.ndarray, checkpoint: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    start = checkpoint // 5
    n = len(a["dates"])
    entry = np.full(n, np.nan, np.float32)
    hi = np.full((n, 78 - start), np.nan, np.float32)
    lo = np.full_like(hi, np.nan)
    cl = np.full_like(hi, np.nan)
    for d, si in enumerate(selected):
        entry[d] = a["open"][int(si), d, start]
        hi[d] = a["high"][int(si), d, start:]
        lo[d] = a["low"][int(si), d, start:]
        cl[d] = a["close"][int(si), d, start:]
    return entry, hi, lo, cl


def gates() -> list[dict]:
    out = [{"gate": "none", "score_bps": 0, "range_bps": 0, "rvol": 0}]
    out += [{"gate": "score", "score_bps": s, "range_bps": 0, "rvol": 0} for s in (25, 50, 100)]
    out += [{"gate": "range", "score_bps": 0, "range_bps": r, "rvol": 0} for r in (100, 200, 300, 500)]
    out += [{"gate": "rvol", "score_bps": 0, "range_bps": 0, "rvol": v} for v in (1, 1.25, 1.5)]
    out += [
        {"gate": "combined", "score_bps": s, "range_bps": r, "rvol": v}
        for s, r, v in itertools.product((25, 50, 100), (100, 300), (1, 1.25))
    ]
    return out


def run(a: dict, out: Path) -> pd.DataFrame:
    dates = a["dates"]
    train = dates <= base.TRAIN_END
    valid = dates >= base.VALID_START
    exits = base.exit_specs()
    rows = []
    ledgers = []
    for cp in CHECKPOINTS:
        for selector in SELECTORS:
            st = state(a, cp, selector)
            entry, hi, lo, cl = selected_paths(a, st["selected"], cp)
            ret_matrix = base.path_returns_gpu(entry, hi, lo, cl, exits)
            for gate_id, gate in enumerate(gates()):
                mask = (
                    np.isfinite(st["score"]) & (st["score"] != 0)
                    & (np.abs(st["score"]) * 10000 >= gate["score_bps"])
                    & (st["range"] * 10000 >= gate["range_bps"])
                    & (st["rvol"] >= gate["rvol"])
                )
                if mask.sum() < 60:
                    continue
                for exit_id, exit_spec in enumerate(exits):
                    gross = ret_matrix[:, exit_id]
                    active = mask & np.isfinite(gross)
                    if active.sum() < 60:
                        continue
                    for cost in base.COSTS:
                        daily = np.zeros(len(dates), np.float64)
                        daily[active] = gross[active] - 2 * cost / 10000
                        rec = {
                            "checkpoint_min": cp, "selector": selector, "gate_id": gate_id, **gate,
                            "exit_id": exit_id, **exit_spec, "cost_bps_side": cost,
                            "trades": int(active.sum()), "trades_per_week": float(active.sum() / len(dates) * 5),
                            "semi_fraction": float(np.mean(st["use_semi"][active])),
                            "mean_gross_trade": float(np.mean(gross[active])),
                        }
                        rec.update({f"full_{k}": v for k, v in base.metrics(dates, daily).items()})
                        rec.update({f"train_{k}": v for k, v in base.metrics(dates[train], daily[train]).items()})
                        rec.update({f"valid_{k}": v for k, v in base.metrics(dates[valid], daily[valid]).items()})
                        rows.append(rec)
    result = pd.DataFrame(rows)
    result["robust_gate"] = (
        result.cost_bps_side.eq(5)
        & result.train_cagr.gt(0) & result.valid_cagr.gt(0)
        & result.full_max_drawdown.gt(-.15)
        & result.full_dd_duration_calendar_days.le(92)
        & result.full_worst_month.gt(-.08)
        & result.full_positive_years.ge(result.full_years - 1)
        & result.trades_per_week.ge(1)
    )
    result.to_csv(out / "cross_pair_grid.csv", index=False)
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
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA required")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    base.SYMBOLS = SYMBOLS
    bars = base.load_bars(args.catalog, out / "bars_cache.parquet")
    a = base.build_arrays(bars)
    result = run(a, out)
    frozen = pd.read_csv(out / "training_frozen_top50.csv")
    best = frozen.sort_values("valid_cagr", ascending=False).iloc[0]
    meta = {
        "sessions": len(a["dates"]), "selectors": len(SELECTORS), "gates": len(gates()),
        "exit_specs": len(base.exit_specs()), "grid_rows": len(result),
        "robust_gate_rows": int(result.robust_gate.sum()), "holdout_access": False,
        "device": torch.cuda.get_device_name(0),
        "gpu_peak_memory_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 3),
        "elapsed_sec": round(time.perf_counter() - t0, 2),
    }
    (out / "run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    report = f"""# Cross-pair large-move selector

**Verdict: {'RESEARCH CANDIDATE' if meta['robust_gate_rows'] else 'NO CANDIDATE'}.**

- Selects between SOXL/SOXS and TQQQ/SQQQ using only completed early bars.
- Pair selectors: fixed semiconductor, fixed Nasdaq, largest early range, largest underlying move, and largest score times opening RVOL.
- {meta['grid_rows']:,} costed cells across timed and full-path TP/SL exits; {meta['robust_gate_rows']} passed the 5 bp/side robustness gate.
- Entry is the next 5-minute open; stop wins same-bar TP/SL collisions; 2026 remained sealed.

Best training-frozen row by validation CAGR: checkpoint {int(best['checkpoint_min'])}m, selector `{best['selector']}`, gate `{best['gate']}`, horizon `{best['horizon']}`, TP {best['tp_bps']:.0f} / SL {best['sl_bps']:.0f} bp. At 5 bp/side: train CAGR {best['train_cagr']:.1%}, validation CAGR {best['valid_cagr']:.1%}, full CAGR {best['full_cagr']:.1%}, max drawdown {best['full_max_drawdown']:.1%}, worst month {best['full_worst_month']:.1%}, recovery {int(best['full_dd_duration_calendar_days'])} days, {best['trades_per_week']:.2f} trades/week.
"""
    (out / "REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
