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
OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0063"
SPECS = [(tier, beta) for tier in (2.0, 3.0, 5.0) for beta in (0.0, 0.25, 0.5)]


def run_variant(spec):
    tier, post_tier_beta = spec
    sys.path.insert(0, str(SRC))
    import run_0060_fixed_risk_budget as engine
    from run_0058_self_financing import solve_target as raw_solver
    from run_0061_partial_compounding import _metrics

    tag = f"tier{tier:g}_beta{post_tier_beta:g}"
    variant_out = OUT / "variants" / tag
    variant_out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASE_OUT / "oos_quotes.parquet", variant_out / "oos_quotes.parquet")
    engine.OUT = variant_out
    engine.MAX_REBALANCE_GROSS = float("inf")

    def policy_solver(cash, current, selected, bid_ratio, ask_ratio, ignored_reserve):
        nav = cash + sum(current.values())
        target_gross = min(nav, tier + post_tier_beta * max(nav - tier, 0.0))
        reserve = max(0.0, nav - target_gross)
        return raw_solver(cash, current, selected, bid_ratio, ask_ratio, reserve)

    engine.solve_target = policy_solver
    engine.replay()
    daily = pd.read_parquet(variant_out / "combined_daily.parquet")
    report = json.loads((variant_out / "report.json").read_text())
    metrics = {
        "tier": tier,
        "post_tier_beta": post_tier_beta,
        **_metrics(daily, pd.Timestamp("2026-04-30")),
        "minimum_cash": report["minimum_cash"],
        "maximum_rebalance_target_gross": report["maximum_rebalance_target_gross"],
        "quote_role_coverage": report["quote_role_coverage"],
    }
    (variant_out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return metrics


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(run_variant, spec): spec for spec in SPECS}
        for future in as_completed(futures):
            rows.append(future.result())
    frame = pd.DataFrame(rows).sort_values(["tier", "post_tier_beta"])
    frame.to_csv(OUT / "metrics.csv", index=False)
    report = {
        "status": "completed",
        "planned_variants": len(SPECS),
        "executed_variants": len(frame),
        "discovery_cutoff": "2026-04-30",
        "maximum_loaded_date": "2026-08-14",
        "holdout_used_for_selection": False,
        "metrics": frame.to_dict("records"),
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(frame[["tier", "post_tier_beta", "discovery_return", "discovery_max_drawdown",
                 "discovery_recent12_return", "discovery_worst_month", "observed_return",
                 "observed_max_drawdown"]].to_string(index=False))


if __name__ == "__main__":
    main()
