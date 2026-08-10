from __future__ import annotations
import json
from datetime import datetime,timezone
import pandas as pd
from control_strategies import CONTROL_IDS,build_control_variants
from run_suite import _load_or_build_fundamentals,_preflight
from suite_core import CAMPAIGNS,COSTS_BPS,CUTOFF,evaluate_weights,jsonable,load_panels,save_variant,write_json

def main():
    panels=load_panels(); preflight=_preflight(panels); f,cov=_load_or_build_fundamentals(panels); preflight["fundamental_coverage"]=cov
    for cid in CONTROL_IDS:
        run=CAMPAIGNS/cid/"runs"/"RUN-0012.yaml"
        if not run.exists(): raise RuntimeError(f"missing {run}")
        variants=build_control_variants(cid,panels,f); records=[]; out=CAMPAIGNS/cid/"artifacts"/"RUN-0012"
        for v in variants:
            for cost in COSTS_BPS:
                metrics,daily,monthly,yearly,symbols=evaluate_weights(v.panel,v.weights,cost,holding=v.holding,execution_lag=v.execution_lag,return_override=v.return_override)
                rec={"campaign_id":cid,"run_id":"RUN-0012","variant_id":v.variant_id,**metrics,"holding":v.holding,"metadata_json":json.dumps(jsonable(v.metadata),sort_keys=True)}; records.append(rec)
                save_variant(out,f"{v.variant_id}__cost_{cost:g}bps",rec,daily,monthly,yearly,symbols,save_detail=float(cost)==2)
        frame=pd.DataFrame(records).sort_values(["cost_bps_per_side","net_simple_return"],ascending=[True,False]); out.mkdir(parents=True,exist_ok=True); frame.to_csv(out/"variant_metrics.csv",index=False)
        best=frame[frame.cost_bps_per_side==2].iloc[0].to_dict(); write_json(out/"execution_report.json",{"campaign_id":cid,"run_id":"RUN-0012","status":"completed","generated_utc":datetime.now(timezone.utc).isoformat(),"variant_count":len(variants),"executed_variant_cost_count":len(frame),"best_at_2bps":jsonable(best),"preflight":preflight,"maximum_loaded_date":str(CUTOFF.date()),"holdout_rows_loaded":0,"fixed_base":1.0,"compounding":False,"broker_margin":False,"frozen_contract":"campaigns/CAM-0600/CONTROL_CONTRACT.yaml","interpretation_blockers":[]})
        print(cid,len(variants),best["variant_id"],f'{best["net_simple_return"]:.4f}',flush=True)
if __name__=="__main__": main()
