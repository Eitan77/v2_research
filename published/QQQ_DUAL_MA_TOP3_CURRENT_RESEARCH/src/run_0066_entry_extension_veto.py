from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0600" / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from suite_core import evaluate_weights, forward_fill_signal_weights, trailing_return
from run_0033_exit_overlays import base_context, summary

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0066"
COST = 9.740340417752536
THRESHOLDS = (0.10, 0.20, 0.30, 0.40, 0.50)


def select_with_veto(score, recent21, mask, signal_indices, threshold):
    raw = np.zeros_like(score)
    current: set[int] = set()
    blocked = 0
    max_substitution_depth = 0
    changed_signals = 0
    for i in signal_indices:
        eligible = np.flatnonzero(mask[i] & np.isfinite(score[i]) & np.isfinite(recent21[i]))
        order = eligible[np.argsort(score[i, eligible], kind="stable")[::-1]]
        chosen = []
        depth = 0
        for c in order:
            depth += 1
            if c not in current and recent21[i, c] > threshold:
                blocked += 1
                continue
            chosen.append(int(c))
            if len(chosen) == 3:
                break
        chosen_set = set(chosen)
        if chosen_set != current:
            changed_signals += 1
        if chosen:
            raw[i, chosen] = 1.0 / len(chosen)
        max_substitution_depth = max(max_substitution_depth, depth)
        current = chosen_set
    return forward_fill_signal_weights(raw, signal_indices), {
        "blocked_ranked_candidates": blocked,
        "maximum_rank_scan_depth": max_substitution_depth,
        "membership_change_signals": changed_signals,
    }


def concentration(p, weights, net_pnl):
    # Execution-lagged daily contribution approximation, consistent across variants.
    lagged = np.vstack([np.zeros((1, weights.shape[1])), weights[:-1]])
    ret = np.divide(p.adj_open[1:], p.adj_open[:-1], out=np.ones_like(p.adj_open[1:]),
                    where=np.isfinite(p.adj_open[1:]) & np.isfinite(p.adj_open[:-1]) & (p.adj_open[:-1] > 0)) - 1
    contrib = np.nansum(lagged[:-1] * ret, axis=0)
    positive = np.sort(contrib[contrib > 0])[::-1]
    top5_share = float(positive[:5].sum() / positive.sum()) if positive.sum() > 0 else np.nan
    return {"maximum_target_weight": float(weights.max()), "top5_positive_share_approx": top5_share}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    p, score, mask, sig, control, _, _ = base_context()
    if str(pd.Timestamp(p.dates.max()).date()) != "2026-04-30" or int(p.readiness.get("holdout_rows_loaded_total", 0)) != 0:
        raise RuntimeError("discovery boundary failed")
    recent21 = trailing_return(p, 21, 0)
    specs = [("control", None)] + [(f"entry_veto_{int(t*100)}pct", t) for t in THRESHOLDS]
    rows = []
    for name, threshold in specs:
        if threshold is None:
            weights, diag = control.copy(), {"blocked_ranked_candidates": 0, "maximum_rank_scan_depth": 3,
                                             "membership_change_signals": -1}
        else:
            weights, diag = select_with_veto(score, recent21, mask, sig, threshold)
        metrics, daily, *_ = evaluate_weights(p, weights, COST, holding="open_to_next_open", execution_lag=1)
        row = {"variant": name, "threshold": threshold, **summary(daily.net_pnl),
               "turnover": float(metrics["total_turnover"]),
               "trade_sessions": int((daily.turnover > 1e-12).sum()),
               "average_utilization": float(weights.sum(axis=1).mean()),
               **diag, **concentration(p, weights, daily.net_pnl)}
        rows.append(row)
        np.save(OUT / f"weights_{name}.npy", weights)
        daily.reset_index().to_parquet(OUT / f"daily_{name}.parquet", index=False)
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / "metrics.csv", index=False)
    report = {"status": "completed_bar_stage", "planned_variants": len(specs), "executed_variants": len(rows),
              "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0,
              "bar_cost_bps_per_turnover": COST, "metrics": rows}
    (OUT / "bar_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(frame.to_string(index=False))


if __name__ == "__main__":
    main()
