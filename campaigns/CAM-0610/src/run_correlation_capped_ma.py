from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"))
from baseline_strategies import eligible, moving_average
from deep_strategies import liquid_mask, trailing_return
from suite_core import CAMPAIGNS, COSTS_BPS, evaluate_weights, load_panels, save_variant

OUT=CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0028"; RUN=CAMPAIGNS/"CAM-0610"/"runs"/"RUN-0028.yaml"

def build(p, threshold):
 score=trailing_return(p,126,21); mask=eligible(p)&(p.adj_close>moving_average(p,200))&liquid_mask(p,.5)
 r=np.log(p.adj_close/np.vstack([np.full((1,p.n_symbols),np.nan),p.adj_close[:-1]])); out=np.zeros_like(score)
 for i in range(200,p.n_dates):
  cols=np.flatnonzero(mask[i]&np.isfinite(score[i])); cols=cols[np.argsort(score[i,cols],kind="stable")[::-1]][:50]
  chosen=[]
  for col in cols:
   if not chosen: chosen.append(col)
   else:
    x=r[i-63:i,col]; y=r[i-63:i,chosen]
    cor=[]
    for k in range(len(chosen)):
     valid=np.isfinite(x)&np.isfinite(y[:,k]); cor.append(np.corrcoef(x[valid],y[valid,k])[0,1] if valid.sum()>=40 else 1.0)
    if np.nanmax(cor)<threshold: chosen.append(col)
   if len(chosen)==10: break
  if chosen: out[i,chosen]=1/len(chosen)
 return out

def main():
 OUT.mkdir(parents=True,exist_ok=True); p=load_panels()["sp500"]; rows=[]
 for threshold in (.4,.6,.8):
  w=build(p,threshold); vid=f"sp500__ma200__daily__top10__corr{threshold:g}"
  for cost in COSTS_BPS:
   m,d,mo,y,s=evaluate_weights(p,w,cost,holding="open_to_next_open",execution_lag=1); rec={"campaign_id":"CAM-0610","run_id":"RUN-0028","variant_id":vid,"correlation_threshold":threshold,**m,"holding":"open_to_next_open"}; rows.append(rec); save_variant(OUT,f"{vid}__cost_{cost:g}bps",rec,d,mo,y,s,save_detail=float(cost)==2)
 frame=pd.DataFrame(rows); frame.to_csv(OUT/"variant_metrics.csv",index=False); at2=frame[frame.cost_bps_per_side==2].sort_values(["recent12_positive_months","recent12_average_month"],ascending=False); best=at2.iloc[0].to_dict()
 report={"status":"completed","run_id":"RUN-0028","selected":best,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"named_symbol_exclusions":False}
 (OUT/"execution_report.json").write_text(json.dumps(report,indent=2,default=str)+"\n"); r=yaml.safe_load(RUN.read_text()); r["status"]="completed"; r["result"]=report; r["decision"]="Quote replay only if diversification materially improves concentration or drawdown without destroying consistency."; RUN.write_text(yaml.safe_dump(r,sort_keys=False)); print(at2.to_string(index=False))

if __name__=="__main__": main()
