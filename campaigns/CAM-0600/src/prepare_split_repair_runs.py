from pathlib import Path
import json
import yaml

ROOT = Path(__file__).resolve().parents[3]
CAMPAIGNS = ROOT / "campaigns"

for number in range(600, 625):
    campaign_id = f"CAM-{number:04d}"
    path = CAMPAIGNS / campaign_id / "runs" / "RUN-0020.yaml"
    payload = {
        "run_id": "RUN-0020",
        "campaign_id": campaign_id,
        "parent_run": "RUN-0008",
        "status": "planned",
        "change": "Re-execute the frozen deep grid after correcting reciprocal cumulative stock split adjustment.",
        "reason": "The inherited adjustment direction created false near-total losses at forward splits and potentially contaminated both signals and returns.",
        "expected_effect": "Remove mechanical split discontinuities without changing strategy definitions.",
        "frozen_contract": "campaigns/CAM-0600/SPLIT_REPAIR_CONTRACT.yaml",
        "configuration": {"discovery_cutoff": "2026-04-30", "holdout_access": False, "fixed_base": 1.0, "compounding": False, "broker_margin": False, "costs_bps_per_side": [-1, 0, 1, 2, 5, 10]},
        "result": None,
        "decision": None,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with (CAMPAIGNS / campaign_id / "WORKLOG.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"run_id": "RUN-0020", "event": "planned", "reason": payload["reason"], "holdout_rows_loaded": 0}) + "\n")
print("froze 25 RUN-0020 records")
