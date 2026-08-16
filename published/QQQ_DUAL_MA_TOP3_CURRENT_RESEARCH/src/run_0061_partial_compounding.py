from __future__ import annotations

import argparse
import json
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "campaigns" / "CAM-0611" / "src"
BASE_OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0060"
OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0061"
BETAS = [0.0, 0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0]
ESCROW_BETAS = [0.25, 0.5, 0.75]


def _metrics(daily: pd.DataFrame, discovery_end: pd.Timestamp):
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily.date)
    discovery = daily[daily.date <= discovery_end]
    observed = daily[daily.date > discovery_end]
    eq = discovery.set_index("date").equity
    dd = eq / eq.cummax().clip(lower=1.0) - 1
    monthly = eq.resample("ME").last().pct_change()
    monthly.iloc[0] = eq[eq.index.to_period("M") == eq.index[0].to_period("M")].iloc[-1] - 1
    yearly = eq.resample("YE").last().pct_change()
    yearly.iloc[0] = eq[eq.index.year == eq.index[0].year].iloc[-1] - 1
    recent_base = eq.loc[eq.index < pd.Timestamp("2025-05-01")].iloc[-1]
    recent = eq.iloc[-1] / recent_base - 1
    obs_base = discovery.equity.iloc[-1]
    obs_dd = observed.equity / pd.concat([pd.Series([obs_base]), observed.equity], ignore_index=True).cummax().iloc[1:].to_numpy() - 1
    obs_ret = observed.equity.iloc[-1] / obs_base - 1
    obs_month = (1 + observed.set_index("date").equity.pct_change().fillna(observed.equity.iloc[0] / obs_base - 1)).resample("ME").prod() - 1
    return {
        "discovery_return": float(eq.iloc[-1] - 1),
        "discovery_max_drawdown": float(-dd.min()),
        "discovery_recent12_return": float(recent),
        "discovery_positive_months": int((monthly > 0).sum()),
        "discovery_negative_months": int((monthly < 0).sum()),
        "discovery_worst_month": float(monthly.min()),
        "discovery_worst_year": float(yearly.min()),
        "ending_cash_discovery": float(discovery.cash.iloc[-1]),
        "ending_gross_discovery": float(discovery.gross_value.iloc[-1]),
        "observed_return": float(obs_ret),
        "observed_max_drawdown": float(-obs_dd.min()),
        "observed_monthly": {str(k.to_period("M")): float(v) for k, v in obs_month.items()},
        "ending_equity": float(daily.equity.iloc[-1]),
        "ending_cash": float(daily.cash.iloc[-1]),
        "ending_gross": float(daily.gross_value.iloc[-1]),
    }


def run_variant(spec):
    family, beta = spec
    sys.path.insert(0, str(SRC))
    import run_0060_fixed_risk_budget as engine
    from run_0058_self_financing import solve_target as raw_solver

    tag = f"{family}_beta{beta:g}"
    variant_out = OUT / "variants" / tag
    variant_out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASE_OUT / "oos_quotes.parquet", variant_out / "oos_quotes.parquet")
    engine.OUT = variant_out
    engine.MAX_REBALANCE_GROSS = float("inf")
    high_water = [1.0]

    def policy_solver(cash, current, selected, bid_ratio, ask_ratio, ignored_reserve):
        nav = cash + sum(current.values())
        high_water[0] = max(high_water[0], nav)
        if family == "equity_linked":
            reserve = (1.0 - beta) * max(nav - 1.0, 0.0)
        else:
            reserve = min(nav, (1.0 - beta) * max(high_water[0] - 1.0, 0.0))
        return raw_solver(cash, current, selected, bid_ratio, ask_ratio, reserve)

    engine.solve_target = policy_solver
    engine.replay()
    daily = pd.read_parquet(variant_out / "combined_daily.parquet")
    report = json.loads((variant_out / "report.json").read_text())
    metrics = {"family": family, "beta": beta, **_metrics(daily, pd.Timestamp("2026-04-30")),
               "minimum_cash": report["minimum_cash"],
               "maximum_rebalance_target_gross": report["maximum_rebalance_target_gross"],
               "quote_role_coverage": report["quote_role_coverage"]}
    (variant_out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return metrics


def run_all():
    OUT.mkdir(parents=True, exist_ok=True)
    specs = [("equity_linked", beta) for beta in BETAS] + [("high_water_escrow", beta) for beta in ESCROW_BETAS]
    rows = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(run_variant, spec): spec for spec in specs}
        for future in as_completed(futures):
            rows.append(future.result())
    frame = pd.DataFrame(rows).sort_values(["family", "beta"])
    frame.to_csv(OUT / "metrics.csv", index=False)
    report = {"status": "completed", "planned_variants": len(specs), "executed_variants": len(frame),
              "maximum_loaded_date": "2026-08-14", "discovery_cutoff": "2026-04-30",
              "holdout_used_for_selection": False, "metrics": frame.to_dict("records")}
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(frame[["family", "beta", "discovery_return", "discovery_max_drawdown", "discovery_recent12_return",
                 "discovery_worst_month", "observed_return", "observed_max_drawdown"]].to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("run",))
    args = parser.parse_args()
    run_all()
