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

from run_0033_exit_overlays import base_context
from run_0054_profit_trims import COST_BPS, extended_summary
from suite_core import evaluate_weights

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0056"


def variant_specs():
    return {
        "control": None,
        "orig10_every5_to25": ("original", [0.05, 0.10, 0.15, 0.20, 0.25], 0.10),
        "orig20_every5_to15": ("original", [0.05, 0.10, 0.15], 0.20),
        "orig20_every5_to25": ("original", [0.05, 0.10, 0.15, 0.20, 0.25], 0.20),
        "orig25_every5_to20": ("original", [0.05, 0.10, 0.15, 0.20], 0.25),
        "orig20_every10_to50": ("original", [0.10, 0.20, 0.30, 0.40, 0.50], 0.20),
        "rem20_every5_to25": ("remaining", [0.05, 0.10, 0.15, 0.20, 0.25], 0.20),
        "rem25_every5_to20": ("remaining", [0.05, 0.10, 0.15, 0.20], 0.25),
        "rem33_every5_to15": ("remaining", [0.05, 0.10, 0.15], 1.0 / 3.0),
        "rem20_every10_to50": ("remaining", [0.10, 0.20, 0.30, 0.40, 0.50], 0.20),
    }


def ladder_weights(p, signal_indices, base, spec):
    if spec is None:
        return base.copy(), Counter()
    basis, thresholds_raw, fraction = spec
    thresholds = np.asarray(thresholds_raw, dtype=float)
    decisions = np.zeros_like(base)
    current = np.zeros(p.n_symbols)
    reference = np.full(p.n_symbols, np.nan)
    original_weight = np.zeros(p.n_symbols)
    stage = np.zeros(p.n_symbols, dtype=int)
    weekly = set(int(x) for x in signal_indices)
    counts = Counter()
    for i in range(len(p.dates)):
        executed = np.zeros(p.n_symbols) if i == 0 else decisions[i - 1].copy()
        opened = (executed > 1e-12) & (current <= 1e-12)
        closed = (executed <= 1e-12) & (current > 1e-12)
        current = executed.copy()
        reference[opened] = p.adj_open[i, opened]
        original_weight[opened] = current[opened]
        stage[opened] = 0
        reference[closed] = np.nan
        original_weight[closed] = 0.0
        stage[closed] = 0
        if (i - 1) in weekly:
            active = current > 1e-12
            reference[active] = p.adj_open[i, active]
            original_weight[active] = current[active]
            stage[active] = 0
        if i in weekly:
            decisions[i] = base[i]
            continue
        target = current.copy()
        valid = (current > 1e-12) & np.isfinite(reference) & (reference > 0) & np.isfinite(p.adj_close[i])
        for c in np.flatnonzero(valid):
            ret = p.adj_close[i, c] / reference[c] - 1.0
            while stage[c] < len(thresholds) and ret >= thresholds[stage[c]]:
                reduction = original_weight[c] * fraction if basis == "original" else target[c] * fraction
                target[c] = max(0.0, target[c] - reduction)
                counts[f"stage_{stage[c]+1}"] += 1
                stage[c] += 1
        decisions[i] = target
    return decisions, counts


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    p, _, _, sig, base, _, _ = base_context()
    if str(pd.Timestamp(p.dates.max()).date()) != "2026-04-30" or int(p.readiness.get("holdout_rows_loaded_total", 0)) != 0:
        raise RuntimeError("readiness failed")
    rows = []
    for name, spec in variant_specs().items():
        w, counts = ladder_weights(p, sig, base, spec)
        if np.nanmax(w.sum(axis=1)) > 1.0 + 1e-12 or np.nanmin(w) < -1e-12:
            raise RuntimeError(f"invalid weights for {name}")
        metrics, daily, *_ = evaluate_weights(p, w, COST_BPS, holding="open_to_next_open", execution_lag=1)
        rows.append({"variant": name, **extended_summary(daily.net_pnl), "turnover": float(metrics["total_turnover"]),
                     "trade_sessions": int((daily.turnover > 1e-12).sum()), "average_utilization": float(w.sum(axis=1).mean()),
                     "trim_counts": dict(counts)})
        np.save(OUT / f"weights_{name}.npy", w)
        daily.reset_index().to_parquet(OUT / f"bar_daily_{name}.parquet", index=False)
    report = {"status": "completed_bar_stage", "planned_variants": len(variant_specs()), "executed_variants": len(rows),
              "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0, "metrics": rows}
    (OUT / "bar_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame(rows).to_csv(OUT / "metrics.csv", index=False)
    print(pd.DataFrame(rows).sort_values("net_simple_return", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
