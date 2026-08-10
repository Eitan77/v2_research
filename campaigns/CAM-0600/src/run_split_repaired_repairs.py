from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import yaml

from repair_strategies import build_repair_variants
from run_suite import _load_or_build_fundamentals, _preflight
from suite_core import CAMPAIGNS, COSTS_BPS, CUTOFF, evaluate_weights, jsonable, load_panels, save_variant, write_json

CAMPAIGN_IDS = ("CAM-0606", "CAM-0608", "CAM-0609", "CAM-0613", "CAM-0617", "CAM-0621")


def freeze() -> None:
    for campaign_id in CAMPAIGN_IDS:
        path = CAMPAIGNS / campaign_id / "runs" / "RUN-0021.yaml"
        payload = {"run_id": "RUN-0021", "campaign_id": campaign_id, "parent_run": "RUN-0020", "status": "planned", "change": "Re-execute the previously frozen mechanism repair family after reciprocal split correction.", "reason": "RUN-0020 still failed or was identity-blocked, and the prior RUN-0010 repair evidence used contaminated stock adjustment.", "expected_effect": "Resolve whether the prior mechanism-specific repair survives corrected stock returns without changing its search grid.", "frozen_contract": "campaigns/CAM-0600/SPLIT_REPAIR_CONTRACT.yaml", "configuration": {"identical_variant_grid_to": "RUN-0010", "discovery_cutoff": "2026-04-30", "holdout_access": False, "fixed_base": 1.0, "compounding": False, "broker_margin": False, "costs_bps_per_side": list(COSTS_BPS)}, "result": None, "decision": None}
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        with (CAMPAIGNS / campaign_id / "WORKLOG.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"run_id": "RUN-0021", "event": "planned", "reason": payload["reason"], "holdout_rows_loaded": 0}) + "\n")


def main() -> None:
    freeze()
    panels = load_panels()
    preflight = _preflight(panels)
    fundamental, coverage = _load_or_build_fundamentals(panels)
    preflight["fundamental_coverage"] = coverage
    for campaign_id in CAMPAIGN_IDS:
        output = CAMPAIGNS / campaign_id / "artifacts" / "RUN-0021"
        variants = build_repair_variants(campaign_id, panels, fundamental)
        records = []
        for variant in variants:
            for cost in COSTS_BPS:
                metrics, daily, monthly, yearly, symbols = evaluate_weights(variant.panel, variant.weights, cost, holding=variant.holding, execution_lag=variant.execution_lag, return_override=variant.return_override)
                record = {"campaign_id": campaign_id, "run_id": "RUN-0021", "variant_id": variant.variant_id, **metrics, "holding": variant.holding, "metadata_json": json.dumps(jsonable(variant.metadata), sort_keys=True)}
                records.append(record)
                save_variant(output, f"{variant.variant_id}__cost_{cost:g}bps", record, daily, monthly, yearly, symbols, save_detail=float(cost) == 2.0)
        frame = pd.DataFrame(records).sort_values(["cost_bps_per_side", "net_simple_return"], ascending=[True, False])
        output.mkdir(parents=True, exist_ok=True)
        frame.to_csv(output / "variant_metrics.csv", index=False)
        best = frame[frame.cost_bps_per_side == 2.0].sort_values("net_simple_return", ascending=False).iloc[0].to_dict()
        report = {"status": "completed", "campaign_id": campaign_id, "run_id": "RUN-0021", "parent_run": "RUN-0020", "generated_utc": datetime.now(timezone.utc).isoformat(), "source_variant_count": len(variants), "executed_variant_cost_count": len(frame), "best_at_2bps": jsonable(best), "maximum_loaded_date": str(CUTOFF.date()), "holdout_rows_loaded": 0, "fixed_base": 1.0, "compounding": False, "broker_margin": False, "preflight": preflight, "frozen_contract": "campaigns/CAM-0600/SPLIT_REPAIR_CONTRACT.yaml"}
        write_json(output / "execution_report.json", report)
        path = CAMPAIGNS / campaign_id / "runs" / "RUN-0021.yaml"
        run = yaml.safe_load(path.read_text(encoding="utf-8")); run["status"] = "completed"; run["result"] = report; run["decision"] = "Requires repaired structured screen before interpretation."; path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
        print(campaign_id, len(variants), best["variant_id"], best["net_simple_return"], flush=True)


if __name__ == "__main__":
    main()
