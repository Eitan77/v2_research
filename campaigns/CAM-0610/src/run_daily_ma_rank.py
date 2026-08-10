from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
import sys
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0600" / "src"))

from deep_strategies import active_trend_rank
from baseline_strategies import moving_average
from suite_core import CAMPAIGNS, COSTS_BPS, evaluate_weights, load_panels, save_variant


OUT = CAMPAIGNS / "CAM-0610" / "artifacts" / "RUN-0024"
RUN = CAMPAIGNS / "CAM-0610" / "runs" / "RUN-0024.yaml"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    panels = load_panels()
    rows = []
    for name in ("sp500", "qqq", "etf"):
        p = panels[name]
        signals = np.arange(p.n_dates)
        for window in (100, 150, 200):
            condition = p.adj_close > moving_average(p, window)
            for top_k in ((3, 5, 10) if name != "etf" else (1, 3, 5)):
                for score in ("momentum", "risk_adjusted"):
                    for gate_name, gate in ((f"ma{window}", condition), ("ungated", np.ones_like(condition, dtype=bool))):
                        weights = active_trend_rank(p, gate, signals, top_k, score)
                        vid = f"{name}__{gate_name}__daily__top{top_k}__{score}"
                        for cost in COSTS_BPS:
                            metrics, daily, monthly, yearly, symbols = evaluate_weights(
                                p, weights, cost, holding="open_to_next_open", execution_lag=1
                            )
                            rec = {"campaign_id": "CAM-0610", "run_id": "RUN-0024", "variant_id": vid,
                                   "panel": name, "sma_window": window, "gate": gate_name,
                                   "top_k": top_k, "rank": score, **metrics, "holding": "open_to_next_open"}
                            rows.append(rec)
                            save_variant(OUT, f"{vid}__cost_{cost:g}bps", rec, daily, monthly, yearly, symbols,
                                         save_detail=float(cost) == 2.0)
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / "variant_metrics.csv", index=False)
    at2 = frame[frame.cost_bps_per_side == 2].copy()
    gated = at2[at2.gate != "ungated"].copy()
    controls = at2[at2.gate == "ungated"].copy()
    controls = controls.drop_duplicates(["panel", "top_k", "rank"])[["panel", "top_k", "rank", "net_simple_return", "maximum_drawdown"]]
    controls = controls.rename(columns={"net_simple_return": "control_net", "maximum_drawdown": "control_dd"})
    comparison = gated.merge(controls, on=["panel", "top_k", "rank"], how="left", validate="many_to_one")
    comparison["incremental_net"] = comparison.net_simple_return - comparison.control_net
    comparison.to_csv(OUT / "matched_controls_2bps.csv", index=False)
    eligible = comparison[(comparison.recent12_average_month > 0) & (comparison.recent12_positive_months >= 7)]
    eligible = eligible.sort_values(["recent12_positive_months", "recent12_average_month", "incremental_net"], ascending=False)
    best = None if eligible.empty else eligible.iloc[0].to_dict()
    report = {"status": "completed", "run_id": "RUN-0024", "variant_cost_tests": int(len(frame)),
              "structured_candidates": int(len(eligible)), "selected": best,
              "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0,
              "fixed_base": 1.0, "broker_margin": False, "direct_short": False,
              "generated_utc": datetime.now(timezone.utc).isoformat()}
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    run = yaml.safe_load(RUN.read_text(encoding="utf-8")); run["status"] = "completed"; run["result"] = report
    run["decision"] = "Quote replay only a daily candidate that is profitable at the low-cost gate and has a nonnegative matched-control contribution."
    RUN.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(eligible.head(12)[["variant_id", "net_simple_return", "maximum_drawdown", "recent12_average_month", "recent12_positive_months", "incremental_net"]].to_string(index=False))


if __name__ == "__main__":
    main()
