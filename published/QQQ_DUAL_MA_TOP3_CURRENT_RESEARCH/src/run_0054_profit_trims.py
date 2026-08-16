from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0600" / "src"))

from run_0033_exit_overlays import base_context, summary
from suite_core import evaluate_weights

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0054"
COST_BPS = 9.740340417752536


def specs() -> dict[str, dict | None]:
    out: dict[str, dict | None] = {"control": None}
    for threshold in (0.05, 0.10, 0.15):
        for fraction in (0.25, 0.50, 0.75):
            out[f"weekly_t{int(threshold*100)}_f{int(fraction*100)}"] = {
                "mode": "weekly", "thresholds": [threshold], "fractions": [fraction]
            }
    for threshold, fraction in ((0.05, 0.50), (0.10, 0.25), (0.10, 0.50), (0.15, 0.50)):
        out[f"persistent_t{int(threshold*100)}_f{int(fraction*100)}"] = {
            "mode": "persistent", "thresholds": [threshold], "fractions": [fraction]
        }
    out["weekly_ladder_5_10_f25"] = {
        "mode": "weekly", "thresholds": [0.05, 0.10], "fractions": [0.25, 0.25]
    }
    out["weekly_ladder_10_20_f25"] = {
        "mode": "weekly", "thresholds": [0.10, 0.20], "fractions": [0.25, 0.25]
    }
    out["persistent_ladder_10_20_f25"] = {
        "mode": "persistent", "thresholds": [0.10, 0.20], "fractions": [0.25, 0.25]
    }
    return out


def build_trim_weights(p, signal_indices, base, spec):
    if spec is None:
        return base.copy(), Counter()
    mode = spec["mode"]
    thresholds = np.asarray(spec["thresholds"], dtype=float)
    fractions = np.asarray(spec["fractions"], dtype=float)
    decisions = np.zeros_like(base)
    current = np.zeros(p.n_symbols)
    reference = np.full(p.n_symbols, np.nan)
    original_weight = np.zeros(p.n_symbols)
    stage = np.zeros(p.n_symbols, dtype=int)
    counts = Counter()
    weekly = set(int(x) for x in signal_indices)

    for i in range(len(p.dates)):
        executed = np.zeros(p.n_symbols) if i == 0 else decisions[i - 1].copy()
        opened = (executed > 1e-12) & (current <= 1e-12)
        closed = (executed <= 1e-12) & (current > 1e-12)
        weekly_execution = (i - 1) in weekly

        reference[opened] = p.adj_open[i, opened]
        original_weight[opened] = executed[opened]
        stage[opened] = 0
        reference[closed] = np.nan
        original_weight[closed] = 0.0
        stage[closed] = 0
        current = executed.copy()

        if mode == "weekly" and weekly_execution:
            active = current > 1e-12
            reference[active] = p.adj_open[i, active]
            original_weight[active] = current[active]
            stage[active] = 0

        if i in weekly:
            target = base[i].copy()
            if mode == "persistent":
                continuing = (current > 1e-12) & (target > 1e-12) & (stage > 0)
                target[continuing] = current[continuing]
            decisions[i] = target
            continue

        target = current.copy()
        held = (current > 1e-12) & np.isfinite(reference) & (reference > 0) & np.isfinite(p.adj_close[i])
        for c in np.flatnonzero(held):
            ret = p.adj_close[i, c] / reference[c] - 1.0
            while stage[c] < len(thresholds) and ret >= thresholds[stage[c]]:
                reduction = original_weight[c] * fractions[stage[c]]
                target[c] = max(0.0, target[c] - reduction)
                counts[f"stage_{stage[c]+1}"] += 1
                stage[c] += 1
        decisions[i] = target

    return decisions, counts


def extended_summary(net: pd.Series) -> dict:
    result = summary(net)
    monthly = net.groupby(net.index.to_period("M")).sum()
    yearly = net.groupby(net.index.year).sum()
    result.update({
        "median_month": float(monthly.median()),
        "average_month": float(monthly.mean()),
        "positive_years": int((yearly > 0).sum()),
        "negative_years": int((yearly < 0).sum()),
    })
    return result


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p, _, _, sig, base, _, _ = base_context()
    if str(pd.Timestamp(p.dates.max()).date()) != "2026-04-30":
        raise RuntimeError("discovery cutoff breached")
    if int(p.readiness.get("holdout_rows_loaded_total", 0)) != 0:
        raise RuntimeError("holdout rows loaded")

    rows = []
    all_specs = specs()
    for name, spec in all_specs.items():
        weights, counts = build_trim_weights(p, sig, base, spec)
        if np.nanmax(weights.sum(axis=1)) > 1.0 + 1e-12 or np.nanmin(weights) < -1e-12:
            raise RuntimeError(f"invalid weights for {name}")
        metrics, daily, *_ = evaluate_weights(
            p, weights, COST_BPS, holding="open_to_next_open", execution_lag=1
        )
        rows.append({
            "variant": name,
            **extended_summary(daily.net_pnl),
            "turnover": float(metrics["total_turnover"]),
            "trade_sessions": int((daily.turnover > 1e-12).sum()),
            "average_utilization": float(weights.sum(axis=1).mean()),
            "trim_counts": dict(counts),
        })
        np.save(OUT / f"weights_{name}.npy", weights)
        daily.reset_index().to_parquet(OUT / f"bar_daily_{name}.parquet", index=False)

    report = {
        "status": "completed_bar_stage",
        "planned_variants": len(all_specs),
        "executed_variants": len(rows),
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "broker_margin": False,
        "decision_timing": "completed_close_next_open",
        "metrics": rows,
    }
    (OUT / "bar_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame(rows).to_csv(OUT / "metrics.csv", index=False)
    print(pd.DataFrame(rows).sort_values("net_simple_return", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
