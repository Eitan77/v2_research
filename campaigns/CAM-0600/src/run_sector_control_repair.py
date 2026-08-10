from __future__ import annotations
import json
from datetime import datetime,timezone
import numpy as np
import pandas as pd
import yaml
from deep_strategies import liquid_mask,rank_long,trend_mask
from baseline_strategies import Variant,moving_average,sector_panel_mask
from run_suite import _preflight
from suite_core import CAMPAIGNS,COSTS_BPS,CUTOFF,evaluate_weights,forward_fill_signal_weights,jsonable,load_panels,month_end_indices,save_variant,trailing_return,write_json

def build(cid,p):
    out=[]; signals=month_end_indices(p.dates); sector=sector_panel_mask(p); spy=p.symbol_to_col["SPY"]
    for formation,skip in ((63,0),(126,0),(126,21),(252,21)):
        score=trailing_return(p,formation,skip)
        for top_k in (1,3,5):
            plain=rank_long(p,score,signals,sector&(score>0),top_k,63)
            if cid=="CAM-0619":
                for winner_ma in (100,150,200):
                    gated=rank_long(p,score,signals,sector&(score>0)&trend_mask(p,winner_ma),top_k,63)
                    out.append(Variant(cid,f"sector__mom{formation}_skip{skip}__top{top_k}__winnerma{winner_ma}",p,gated,"open_to_next_open",1,{"winner_ma":winner_ma,"matched_plain_variant":f"mom{formation}_skip{skip}_top{top_k}"}))
            else:
                for market_ma in (100,150,200):
                    ma=moving_average(p,market_ma); raw=np.zeros_like(plain); cash=p.symbol_to_col["BIL"]
                    for i in signals:
                        if p.adj_close[i,spy]>ma[i,spy]: raw[i]=plain[i]
                        else: raw[i,cash]=1.0
                    gated=forward_fill_signal_weights(raw,signals)
                    out.append(Variant(cid,f"sector__mom{formation}_skip{skip}__top{top_k}__marketma{market_ma}",p,gated,"open_to_next_open",1,{"market_ma":market_ma,"fallback":"BIL","matched_plain_variant":f"mom{formation}_skip{skip}_top{top_k}"}))
    return out

def main():
    panels=load_panels(); preflight=_preflight(panels); p=panels["etf"]
    for cid in ("CAM-0619","CAM-0620"):
        old=CAMPAIGNS/cid/"runs"/"RUN-0012.yaml"; y=yaml.safe_load(old.read_text()); y["status"]="invalid"; y["result"]={"interpretation_blocker":"Executed controls omitted the contract-required MA gate; artifacts preserved under RUN-0012_INVALID_CONTRACT_MISMATCH."}; y["decision"]="Do not interpret."; old.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
        src=CAMPAIGNS/cid/"artifacts"/"RUN-0012"; dst=CAMPAIGNS/cid/"artifacts"/"RUN-0012_INVALID_CONTRACT_MISMATCH"
        if dst.exists(): raise RuntimeError(dst)
        src.rename(dst)
        run=CAMPAIGNS/cid/"runs"/"RUN-0013.yaml"; payload={"run_id":"RUN-0013","campaign_id":cid,"parent_run":"RUN-0012","status":"planned","change":"Correct matched sector MA-gate controls.","reason":"RUN-0012 did not execute the frozen winner or market MA gate.","expected_effect":"Measure incremental return and risk of the source gate against identical relative momentum.","frozen_contract":"campaigns/CAM-0600/CONTROL_CONTRACT.yaml","configuration":{"winner_ma_windows":[100,150,200] if cid=="CAM-0619" else None,"market_ma_windows":[100,150,200] if cid=="CAM-0620" else None,"costs_bps_per_side":[-1,0,1,2,5,10],"holdout_access":False},"result":None,"decision":None}; run.write_text(yaml.safe_dump(payload,sort_keys=False),encoding="utf-8")
        variants=build(cid,p); out=CAMPAIGNS/cid/"artifacts"/"RUN-0013"; rows=[]
        for v in variants:
            for cost in COSTS_BPS:
                metrics,daily,monthly,yearly,symbols=evaluate_weights(p,v.weights,cost,holding=v.holding,execution_lag=1); rec={"campaign_id":cid,"run_id":"RUN-0013","variant_id":v.variant_id,**metrics,"holding":v.holding,"metadata_json":json.dumps(jsonable(v.metadata),sort_keys=True)}; rows.append(rec); save_variant(out,f"{v.variant_id}__cost_{cost:g}bps",rec,daily,monthly,yearly,symbols,save_detail=cost==2)
        frame=pd.DataFrame(rows).sort_values(["cost_bps_per_side","net_simple_return"],ascending=[True,False]); frame.to_csv(out/"variant_metrics.csv",index=False); best=frame[frame.cost_bps_per_side==2].iloc[0].to_dict(); write_json(out/"execution_report.json",{"status":"completed","campaign_id":cid,"run_id":"RUN-0013","generated_utc":datetime.now(timezone.utc).isoformat(),"variant_count":len(variants),"executed_variant_cost_count":len(frame),"best_at_2bps":jsonable(best),"preflight":preflight,"maximum_loaded_date":str(CUTOFF.date()),"holdout_rows_loaded":0,"interpretation_blockers":[]}); print(cid,best["variant_id"],best["net_simple_return"])
if __name__=="__main__": main()
