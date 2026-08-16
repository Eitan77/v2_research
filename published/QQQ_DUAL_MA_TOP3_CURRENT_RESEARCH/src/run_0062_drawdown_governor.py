from __future__ import annotations

import json
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "campaigns" / "CAM-0611" / "src"
BASE_OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0060"
OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0062"
SPECS = (
    [(1.0, low, trigger) for trigger in (0.05, 0.10, 0.15) for low in (0.0, 0.25, 0.5)]
    + [(0.75, 0.25, 0.10), (0.75, 0.5, 0.10)]
)


def run_variant(spec):
    normal, defensive_beta, trigger = spec
    sys.path.insert(0, str(SRC))
    import run_0060_fixed_risk_budget as engine
    from run_0058_self_financing import solve_target as raw_solver
    from run_0061_partial_compounding import _metrics

    tag = f"normal{normal:g}_low{defensive_beta:g}_trigger{trigger:g}"
    variant_out = OUT / "variants" / tag
    variant_out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASE_OUT / "oos_quotes.parquet", variant_out / "oos_quotes.parquet")
    engine.OUT = variant_out
    engine.MAX_REBALANCE_GROSS = float("inf")
    state = {"high_water": 1.0, "defensive": False, "flips": 0, "calls": 0, "defensive_calls": 0,
             "discovery_flips": 0, "discovery_defensive_calls": 0}

    def policy_solver(cash, current, selected, bid_ratio, ask_ratio, ignored_reserve):
        nav = cash + sum(current.values())
        state["high_water"] = max(state["high_water"], nav)
        drawdown = nav / state["high_water"] - 1.0
        prior = state["defensive"]
        if not state["defensive"] and drawdown <= -trigger:
            state["defensive"] = True
        elif state["defensive"] and drawdown >= -(trigger / 2.0):
            state["defensive"] = False
        if state["defensive"] != prior:
            state["flips"] += 1
            if state["calls"] < 316:
                state["discovery_flips"] += 1
        beta = defensive_beta if state["defensive"] else normal
        if state["defensive"]:
            state["defensive_calls"] += 1
            if state["calls"] < 316:
                state["discovery_defensive_calls"] += 1
        state["calls"] += 1
        reserve = (1.0 - beta) * max(nav - 1.0, 0.0)
        return raw_solver(cash, current, selected, bid_ratio, ask_ratio, reserve)

    engine.solve_target = policy_solver
    engine.replay()
    daily = pd.read_parquet(variant_out / "combined_daily.parquet")
    report = json.loads((variant_out / "report.json").read_text())
    metrics = {"normal_beta": normal, "defensive_beta": defensive_beta, "trigger": trigger,
               "recovery": trigger / 2.0, **_metrics(daily, pd.Timestamp("2026-04-30")),
               "discovery_state_flips": state["discovery_flips"],
               "discovery_defensive_rebalances": state["discovery_defensive_calls"],
               "total_state_flips": state["flips"], "total_defensive_rebalances": state["defensive_calls"],
               "minimum_cash": report["minimum_cash"], "quote_role_coverage": report["quote_role_coverage"]}
    (variant_out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return metrics


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(run_variant, spec): spec for spec in SPECS}
        for future in as_completed(futures):
            rows.append(future.result())
    frame = pd.DataFrame(rows).sort_values(["normal_beta", "trigger", "defensive_beta"])
    frame.to_csv(OUT / "metrics.csv", index=False)
    report = {"status": "completed", "planned_variants": len(SPECS), "executed_variants": len(frame),
              "discovery_cutoff": "2026-04-30", "maximum_loaded_date": "2026-08-14",
              "holdout_used_for_selection": False, "metrics": frame.to_dict("records")}
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(frame[["normal_beta", "defensive_beta", "trigger", "discovery_return", "discovery_max_drawdown",
                 "discovery_recent12_return", "discovery_worst_month", "discovery_state_flips",
                 "observed_return", "observed_max_drawdown"]].to_string(index=False))


if __name__ == "__main__":
    main()
