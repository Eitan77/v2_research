from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import yaml

from repair_strategies import build_repair_variants
from run_suite import _load_or_build_fundamentals, _preflight
from suite_core import CAMPAIGNS, COSTS_BPS, CUTOFF, evaluate_weights, jsonable, load_panels, save_variant, write_json


CAMPAIGN_IDS=("CAM-0606","CAM-0608","CAM-0609","CAM-0613","CAM-0617","CAM-0621")


def freeze_records():
    for campaign_id in CAMPAIGN_IDS:
        path=CAMPAIGNS/campaign_id/"runs"/"RUN-0010.yaml"
        if path.exists(): raise RuntimeError(f"refusing to overwrite {path}")
        payload={"run_id":"RUN-0010","campaign_id":campaign_id,"parent_run":"RUN-0008","status":"planned","change":"Mechanism-specific repair of the diagnosed RUN-0008 failure.","reason":"The first deep structured screen failed or was identity-blocked.","expected_effect":"Test whether a slower horizon, stronger causal setup, or correct economic-pair identity improves edge per turnover without ticker mining.","frozen_contract":"campaigns/CAM-0600/REPAIR_CONTRACT.yaml","configuration":{"discovery_cutoff":"2026-04-30","holdout_access":False,"fixed_base":1.0,"broker_margin":False,"costs_bps_per_side":[-1,0,1,2,5,10]},"result":None,"decision":None}
        path.write_text(yaml.safe_dump(payload,sort_keys=False),encoding="utf-8")


def main():
    freeze_records()
    panels=load_panels(); preflight=_preflight(panels); fundamental,coverage=_load_or_build_fundamentals(panels); preflight["fundamental_coverage"]=coverage
    for campaign_id in CAMPAIGN_IDS:
        output=CAMPAIGNS/campaign_id/"artifacts"/"RUN-0010"; variants=build_repair_variants(campaign_id,panels,fundamental); records=[]
        for v in variants:
            for cost in COSTS_BPS:
                metrics,daily,monthly,yearly,symbols=evaluate_weights(v.panel,v.weights,cost,holding=v.holding,execution_lag=v.execution_lag,return_override=v.return_override)
                record={"campaign_id":campaign_id,"run_id":"RUN-0010","variant_id":v.variant_id,**metrics,"holding":v.holding,"metadata_json":json.dumps(jsonable(v.metadata),sort_keys=True)}; records.append(record)
                save_variant(output,f"{v.variant_id}__cost_{cost:g}bps",record,daily,monthly,yearly,symbols,save_detail=float(cost)==2)
        frame=pd.DataFrame(records).sort_values(["cost_bps_per_side","net_simple_return"],ascending=[True,False]); output.mkdir(parents=True,exist_ok=True); frame.to_csv(output/"variant_metrics.csv",index=False)
        best=frame[frame.cost_bps_per_side==2].sort_values("net_simple_return",ascending=False).iloc[0].to_dict()
        write_json(output/"execution_report.json",{"status":"completed","campaign_id":campaign_id,"run_id":"RUN-0010","parent_run":"RUN-0008","generated_utc":datetime.now(timezone.utc).isoformat(),"source_variant_count":len(variants),"executed_variant_cost_count":len(frame),"best_at_2bps":best,"maximum_loaded_date":str(CUTOFF.date()),"holdout_rows_loaded":0,"fixed_base":1.0,"broker_margin":False,"preflight":preflight,"frozen_contract":str(CAMPAIGNS/"CAM-0600"/"REPAIR_CONTRACT.yaml")})
        print(campaign_id,len(variants),best["variant_id"],best["net_simple_return"],flush=True)


if __name__=="__main__": main()
