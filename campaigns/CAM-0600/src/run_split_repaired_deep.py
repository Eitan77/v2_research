from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import pandas as pd
import yaml

from deep_strategies import build_deep_variants
from run_suite import _load_or_build_fundamentals, _preflight
from suite_core import CAMPAIGNS, COSTS_BPS, CUTOFF, evaluate_weights, jsonable, load_panels, save_variant, semantic_fixtures, write_json


def run_campaign(campaign_id, panels, fundamental, preflight):
    run_path = CAMPAIGNS / campaign_id / "runs" / "RUN-0020.yaml"
    run_record = yaml.safe_load(run_path.read_text(encoding="utf-8"))
    if run_record.get("status") != "planned":
        raise RuntimeError(f"RUN-0020 is not frozen planned for {campaign_id}")
    output = CAMPAIGNS / campaign_id / "artifacts" / "RUN-0020"
    variants = build_deep_variants(campaign_id, panels, fundamental)
    records = []
    for variant in variants:
        for cost in COSTS_BPS:
            metrics, daily, monthly, yearly, symbols = evaluate_weights(variant.panel, variant.weights, cost, holding=variant.holding, execution_lag=variant.execution_lag, return_override=variant.return_override)
            record = {"campaign_id": campaign_id, "run_id": "RUN-0020", "variant_id": variant.variant_id, **metrics, "holding": variant.holding, "metadata_json": json.dumps(jsonable(variant.metadata), sort_keys=True)}
            records.append(record)
            save_variant(output, f"{variant.variant_id}__cost_{cost:g}bps", record, daily, monthly, yearly, symbols, save_detail=float(cost) == 2.0)
    frame = pd.DataFrame(records).sort_values(["cost_bps_per_side", "net_simple_return"], ascending=[True, False])
    output.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output / "variant_metrics.csv", index=False)
    best = frame[frame.cost_bps_per_side == 2.0].sort_values("net_simple_return", ascending=False).iloc[0].to_dict()
    report = {"campaign_id": campaign_id, "run_id": "RUN-0020", "parent_run": "RUN-0008", "status": "completed", "generated_utc": datetime.now(timezone.utc).isoformat(), "source_variant_count": len(variants), "executed_variant_cost_count": len(frame), "best_at_2bps": jsonable(best), "maximum_loaded_date": str(CUTOFF.date()), "holdout_rows_loaded": 0, "fixed_base": 1.0, "compounding": False, "broker_margin": False, "preflight": preflight, "semantic_fixtures": semantic_fixtures(), "frozen_contract": "campaigns/CAM-0600/SPLIT_REPAIR_CONTRACT.yaml", "prior_run_interpretation": "invalid_split_adjustment"}
    write_json(output / "execution_report.json", report)
    run_record["status"] = "completed"
    run_record["result"] = report
    run_record["decision"] = "Requires repaired structured selection and all downstream execution/control audits before interpretation."
    run_path.write_text(yaml.safe_dump(run_record, sort_keys=False), encoding="utf-8")
    with (CAMPAIGNS / campaign_id / "WORKLOG.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"run_id": "RUN-0020", "event": "completed", "best_at_2bps": best["variant_id"], "net_simple_return": best["net_simple_return"], "holdout_rows_loaded": 0}) + "\n")
    print(f"{campaign_id}: {len(variants)} variants; best 2bp {best['variant_id']} {best['net_simple_return']:.6f}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", action="append")
    args = parser.parse_args()
    campaigns = args.campaign or [f"CAM-{n:04d}" for n in range(600, 625)]
    panels = load_panels()
    preflight = _preflight(panels)
    fundamental, coverage = _load_or_build_fundamentals(panels)
    preflight["fundamental_coverage"] = coverage
    for campaign_id in campaigns:
        run_campaign(campaign_id, panels, fundamental, preflight)


if __name__ == "__main__":
    main()
