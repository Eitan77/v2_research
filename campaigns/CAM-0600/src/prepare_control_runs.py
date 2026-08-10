from __future__ import annotations
import yaml
from control_strategies import CONTROL_IDS
from suite_core import CAMPAIGNS

for cid in CONTROL_IDS:
    p=CAMPAIGNS/cid/"runs"/"RUN-0012.yaml"
    if p.exists(): raise RuntimeError(f"refusing to overwrite {p}")
    payload={"run_id":"RUN-0012","campaign_id":cid,"parent_run":"RUN-0011" if cid in {"CAM-0617"} else "RUN-0009","status":"planned","change":"Frozen simpler-control and component-ablation challenge.","reason":"Determine whether the adapted survivor adds value beyond simpler exposures and survives breadth/leverage neighborhoods.","expected_effect":"Retain only economically distinct, stable mechanisms; relabel or retire duplicative complexity.","frozen_contract":"campaigns/CAM-0600/CONTROL_CONTRACT.yaml","configuration":{"costs_bps_per_side":[-1,0,1,2,5,10],"cutoff":"2026-04-30","holdout_access":False,"capital_base":1.0},"result":None,"decision":None}
    p.write_text(yaml.safe_dump(payload,sort_keys=False),encoding="utf-8")
print(f"frozen {len(CONTROL_IDS)} run records")
