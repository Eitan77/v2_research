from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0600" / "src"))

from run_0033_exit_overlays import base_context
from run_0054_profit_trims import COST_BPS, extended_summary
from run_0056_profit_ladders import ladder_weights
from suite_core import evaluate_weights

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0057"


def levels(step: int, end: int):
    return [x / 100.0 for x in range(step, end + 1, step)]


def variant_specs():
    return {
        "control": None,
        "orig5_every1_to10_leave50": ("original", levels(1, 10), 0.05),
        "orig5_every1_to20_exit100": ("original", levels(1, 20), 0.05),
        "orig2p5_every1_to20_leave50": ("original", levels(1, 20), 0.025),
        "rem5_every1_to20_leave35p8": ("remaining", levels(1, 20), 0.05),
        "orig5_every2_to20_leave50": ("original", levels(2, 20), 0.05),
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    p, _, _, sig, base, _, _ = base_context()
    if str(pd.Timestamp(p.dates.max()).date()) != "2026-04-30" or int(p.readiness.get("holdout_rows_loaded_total", 0)) != 0:
        raise RuntimeError("readiness failed")
    rows = []
    for name, spec in variant_specs().items():
        w, counts = ladder_weights(p, sig, base, spec)
        metrics, daily, *_ = evaluate_weights(p, w, COST_BPS, holding="open_to_next_open", execution_lag=1)
        rows.append({"variant": name, **extended_summary(daily.net_pnl), "turnover": float(metrics["total_turnover"]),
                     "trade_sessions": int((daily.turnover > 1e-12).sum()), "average_utilization": float(w.sum(axis=1).mean()),
                     "trim_events": int(sum(counts.values())), "trim_counts": dict(counts)})
        np.save(OUT / f"weights_{name}.npy", w)
        daily.reset_index().to_parquet(OUT / f"bar_daily_{name}.parquet", index=False)
    report = {"status": "completed_bar_stage", "planned_variants": len(variant_specs()), "executed_variants": len(rows),
              "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0, "metrics": rows}
    (OUT / "bar_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame(rows).to_csv(OUT / "metrics.csv", index=False)
    print(pd.DataFrame(rows).sort_values("net_simple_return", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
