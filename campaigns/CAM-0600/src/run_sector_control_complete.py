from __future__ import annotations
import json
from datetime import datetime,timezone
import numpy as np
import pandas as pd
import yaml
from baseline_strategies import Variant,moving_average,sector_panel_mask
from deep_strategies import rank_long,trend_mask
from run_suite import _preflight
from suite_core import CAMPAIGNS,COSTS_BPS,CUTOFF,evaluate_weights,forward_fill_signal_weights,jsonable,load_panels,month_end_indices,save_variant,trailing_return,write_json

def variants(cid,p):
 out=[]; sig=month_end_indices(p.dates); sector=sector_panel_mask(p); spy=p.symbol_to_col["SPY"]
 for formation,skip in ((63,0),(126,0),(126,21),(252,21)):
  score=trailing_return(p,formation,skip)
  for top in (1,3,5):
   plain=rank_long(p,score,sig,sector&(score>0),top,63); out.append(Variant(cid,f"sector__mom{formation}_skip{skip}__top{top}__plain",p,plain,"open_to_next_open",1,{"gate":"none"}))
   for window in (100,150,200):
    if cid=="CAM-0619": w=rank_long(p,score,sig,sector&(score>0)&trend_mask(p,window),top,63); kind="winnerma"
    else:
     ma=moving_average(p,window); raw=np.zeros_like(plain); cash=p.symbol_to_col["BIL"]
     for i in sig:
      if p.adj_close[i,spy]>ma[i,spy]: raw[i]=plain[i]
      else: raw[i,cash]=1
     w=forward_fill_signal_weights(raw,sig); kind="marketma"
    out.append(Variant(cid,f"sector__mom{formation}_skip{skip}__top{top}__{kind}{window}",p,w,"open_to_next_open",1,{"gate":kind,"window":window}))
 return out

def main():
 panels=load_panels(); preflight=_preflight(panels); p=panels["etf"]
 for cid in ("CAM-0619","CAM-0620"):
  old=CAMPAIGNS/cid/"runs"/"RUN-0013.yaml"; y=yaml.safe_load(old.read_text()); y["status"]="invalid"; y["result"]={"interpretation_blocker":"Matched plain control was not saved in the same reconciled run."}; y["decision"]="Do not interpret; rerun complete in RUN-0014."; old.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
  src=CAMPAIGNS/cid/"artifacts"/"RUN-0013"; dst=CAMPAIGNS/cid/"artifacts"/"RUN-0013_INVALID_MISSING_MATCHED_PLAIN"; src.rename(dst)
  run=CAMPAIGNS/cid/"runs"/"RUN-0014.yaml"; payload={"run_id":"RUN-0014","campaign_id":cid,"parent_run":"RUN-0013","status":"planned","change":"Complete reconciled source gate and matched plain controls.","reason":"Prior repair omitted the plain control from the same executed record.","expected_effect":"Measure gate increment without cross-run ambiguity.","frozen_contract":"campaigns/CAM-0600/CONTROL_CONTRACT.yaml","configuration":{"formation_skip":[[63,0],[126,0],[126,21],[252,21]],"top_k":[1,3,5],"ma_windows":[100,150,200],"include_plain":True,"costs_bps_per_side":[-1,0,1,2,5,10],"holdout_access":False},"result":None,"decision":None}; run.write_text(yaml.safe_dump(payload,sort_keys=False),encoding="utf-8")
  out=CAMPAIGNS/cid/"artifacts"/"RUN-0014"; rows=[]; vv=variants(cid,p)
  for v in vv:
   for cost in COSTS_BPS:
    metrics,daily,monthly,yearly,symbols=evaluate_weights(p,v.weights,cost,holding=v.holding,execution_lag=1); rec={"campaign_id":cid,"run_id":"RUN-0014","variant_id":v.variant_id,**metrics,"holding":v.holding,"metadata_json":json.dumps(jsonable(v.metadata),sort_keys=True)}; rows.append(rec); save_variant(out,f"{v.variant_id}__cost_{cost:g}bps",rec,daily,monthly,yearly,symbols,save_detail=cost==2)
  frame=pd.DataFrame(rows).sort_values(["cost_bps_per_side","net_simple_return"],ascending=[True,False]); frame.to_csv(out/"variant_metrics.csv",index=False); best=frame[frame.cost_bps_per_side==2].iloc[0].to_dict(); write_json(out/"execution_report.json",{"status":"completed","run_id":"RUN-0014","campaign_id":cid,"generated_utc":datetime.now(timezone.utc).isoformat(),"variant_count":len(vv),"executed_variant_cost_count":len(frame),"best_at_2bps":jsonable(best),"preflight":preflight,"maximum_loaded_date":str(CUTOFF.date()),"holdout_rows_loaded":0,"interpretation_blockers":[]}); print(cid,best["variant_id"],best["net_simple_return"])
if __name__=="__main__": main()
