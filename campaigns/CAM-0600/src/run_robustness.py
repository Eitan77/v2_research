from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_adaptations import CAMPAIGN_IDS, execution_qualified
from suite_core import CAMPAIGNS, write_json


SHARED = CAMPAIGNS / "CAM-0600" / "artifacts" / "shared"


def daily_path(campaign_id: str, variant_id: str) -> Path:
    safe = f"{variant_id}__cost_2bps".replace("/", "_").replace(":", "_")
    return CAMPAIGNS / campaign_id / "artifacts" / "RUN-0002" / "variants" / safe / "daily.parquet"


def drawdown(net: pd.Series) -> float:
    equity = 1.0 + net.cumsum()
    peak = equity.cummax()
    return float(((peak-equity)/peak).max()) if len(equity) else 0.0


def walk_forward(campaign_id: str, metrics: pd.DataFrame) -> dict:
    central = metrics[(metrics["cost_bps_per_side"] == 2.0) & metrics.apply(execution_qualified, axis=1)].copy()
    ledgers = {}
    for variant_id in central["variant_id"].unique():
        frame = pd.read_parquet(daily_path(campaign_id, str(variant_id)))
        frame["date"] = pd.to_datetime(frame["date"])
        ledgers[str(variant_id)] = frame.set_index("date").sort_index()
    if not ledgers:
        return {"status": "not_applicable_no_execution_qualified_variant", "net_return": None, "years": []}
    all_years = sorted(set().union(*(set(x.index.year) for x in ledgers.values())))
    selections = []
    realized = []
    for year in all_years[1:]:
        start = pd.Timestamp(f"{year}-01-01")
        scores = []
        for variant_id, frame in ledgers.items():
            prior = frame[frame.index < start]
            active = int((prior["gross_exposure"] > 1e-12).sum())
            if active < 50:
                continue
            scores.append((float(prior["net_pnl"].sum()), -drawdown(prior["net_pnl"]), variant_id))
        if not scores:
            continue
        scores.sort(reverse=True)
        variant_id = scores[0][2]
        year_frame = ledgers[variant_id][ledgers[variant_id].index.year == year]
        if year_frame.empty:
            continue
        net = float(year_frame["net_pnl"].sum())
        realized.append(net)
        selections.append({"year": int(year), "selected_variant": variant_id,
                           "prior_net_return": scores[0][0], "year_net_return": net})
    return {
        "status": "completed" if selections else "insufficient_prior_activity",
        "net_return": float(sum(realized)) if realized else None,
        "positive_years": int(sum(x > 0 for x in realized)),
        "negative_years": int(sum(x < 0 for x in realized)),
        "years": selections,
    }


def main() -> None:
    adaptation = pd.read_csv(SHARED / "adaptation_summary.csv").set_index("campaign_id")
    rows = []
    for campaign_id in CAMPAIGN_IDS:
        metrics = pd.read_csv(CAMPAIGNS / campaign_id / "artifacts" / "RUN-0002" / "variant_metrics.csv")
        wf = walk_forward(campaign_id, metrics)
        row = adaptation.loc[campaign_id]
        qualified = pd.notna(row["selected_executable_variant"])
        if not qualified and float(row["raw_best_2bps_return"]) > 0:
            decision = "execution_blocked_signal_only"
        elif not qualified or float(row.get("selected_2bps_return", -1) or -1) <= 0:
            decision = "mechanism_failed_after_adaptation"
        elif bool(row["basic_candidate_screen"]) and float(row["positive_variant_fraction_at_2bps"]) >= .50 and (wf["net_return"] or 0) > 0:
            decision = "robust_development_candidate_quote_gate"
        else:
            decision = "profitable_but_fragile_quote_gate"
        report = {
            "campaign_id": campaign_id,
            "run_id": "RUN-0003",
            "parent_run": "RUN-0002",
            "status": "completed",
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "development_only": True,
            "holdout_rows_loaded": 0,
            "maximum_loaded_date": "2026-04-30",
            "fixed_variant_diagnostics": row.to_dict(),
            "walk_forward_parameter_selection": wf,
            "decision": decision,
            "promotion_ready": False,
        }
        output = CAMPAIGNS / campaign_id / "artifacts" / "RUN-0003"
        write_json(output / "robustness_report.json", report)
        rows.append({"campaign_id": campaign_id, "decision": decision,
                     "walk_forward_net_return": wf["net_return"],
                     "walk_forward_positive_years": wf.get("positive_years"),
                     "walk_forward_negative_years": wf.get("negative_years"),
                     "selected_variant": row["selected_executable_variant"],
                     "selected_2bps_return": row["selected_2bps_return"],
                     "post2024_return": row["post2024_return"],
                     "maximum_drawdown": row["maximum_drawdown"],
                     "positive_variant_fraction": row["positive_variant_fraction_at_2bps"]})
    summary = pd.DataFrame(rows)
    summary.to_csv(SHARED / "robustness_summary.csv", index=False)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
