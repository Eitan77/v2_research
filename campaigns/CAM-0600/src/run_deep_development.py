from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import pandas as pd

from deep_strategies import build_deep_variants
from run_suite import CAMPAIGN_IDS, _load_or_build_fundamentals, _preflight
from suite_core import CAMPAIGNS, COSTS_BPS, CUTOFF, evaluate_weights, jsonable, load_panels, save_variant, write_json


def run_campaign(campaign_id, panels, fundamental, preflight):
    run_path = CAMPAIGNS / campaign_id / "runs" / "RUN-0008.yaml"
    if not run_path.exists():
        raise RuntimeError(f"missing frozen run record {run_path}")
    output = CAMPAIGNS / campaign_id / "artifacts" / "RUN-0008"
    variants = build_deep_variants(campaign_id, panels, fundamental)
    records = []
    for variant in variants:
        for cost in COSTS_BPS:
            metrics, daily, monthly, yearly, symbols = evaluate_weights(
                variant.panel,
                variant.weights,
                cost,
                holding=variant.holding,
                execution_lag=variant.execution_lag,
                return_override=variant.return_override,
            )
            record = {
                "campaign_id": campaign_id,
                "run_id": "RUN-0008",
                "variant_id": variant.variant_id,
                **metrics,
                "holding": variant.holding,
                "metadata_json": json.dumps(jsonable(variant.metadata), sort_keys=True),
            }
            records.append(record)
            save_variant(
                output,
                f"{variant.variant_id}__cost_{cost:g}bps",
                record,
                daily,
                monthly,
                yearly,
                symbols,
                save_detail=float(cost) == 2.0,
            )
    frame = pd.DataFrame(records).sort_values(["cost_bps_per_side", "net_simple_return"], ascending=[True, False])
    output.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output / "variant_metrics.csv", index=False)
    at_two = frame[frame["cost_bps_per_side"] == 2.0].sort_values("net_simple_return", ascending=False)
    best = at_two.iloc[0].to_dict()
    report = {
        "campaign_id": campaign_id,
        "run_id": "RUN-0008",
        "parent_run": "RUN-0003",
        "status": "completed",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source_variant_count": int(len(variants)),
        "executed_variant_cost_count": int(len(frame)),
        "costs_bps_per_side": list(COSTS_BPS),
        "best_at_2bps": jsonable(best),
        "maximum_loaded_date": str(CUTOFF.date()),
        "holdout_rows_loaded": 0,
        "fixed_base": 1.0,
        "compounding": False,
        "broker_margin": False,
        "preflight": preflight,
        "frozen_contract": str(CAMPAIGNS / "CAM-0600" / "DEEP_DEVELOPMENT_CONTRACT.yaml"),
        "interpretation_blockers": [],
    }
    write_json(output / "execution_report.json", report)
    print(f"{campaign_id}: {len(variants)} variants; best 2bp {best['variant_id']} {best['net_simple_return']:.6f}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--campaign", choices=CAMPAIGN_IDS)
    group.add_argument("--all", action="store_true")
    parser.add_argument("--start", choices=CAMPAIGN_IDS)
    args = parser.parse_args()
    selected = CAMPAIGN_IDS if args.all else (args.campaign,)
    if args.start:
        if not args.all:
            parser.error("--start requires --all")
        selected = tuple(x for x in selected if x >= args.start)
    panels = load_panels()
    preflight = _preflight(panels)
    fundamental, coverage = _load_or_build_fundamentals(panels)
    preflight["fundamental_coverage"] = coverage
    for campaign_id in selected:
        run_campaign(campaign_id, panels, fundamental, preflight)


if __name__ == "__main__":
    main()
