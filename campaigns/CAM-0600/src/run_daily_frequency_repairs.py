from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd, yaml

ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"))
from baseline_strategies import alpha_combo_weights, multiple_cluster_weights, weighted_regression_weights, moving_average
from deep_strategies import active_trend_rank, concentrate_positive, liquid_mask, trend_mask
from run_suite import _load_or_build_fundamentals
from suite_core import CAMPAIGNS, COSTS_BPS, evaluate_weights, load_panels, save_variant

IDS=("CAM-0608","CAM-0609","CAM-0611","CAM-0612","CAM-0617")

def variants(cid,panels,f):
 out=[]
 if cid in ("CAM-0608","CAM-0609"):
  for name in ("sp500","qqq"):
   p=panels[name]
   for lookback in (5,10,20):
    raw,rep=(multiple_cluster_weights(p,panels["etf"],126,lookback) if cid=="CAM-0608" else weighted_regression_weights(p,f[name],lookback,126))
    for k in (3,10):
     for trend in (False,True):
      mask=liquid_mask(p,.5)&(trend_mask(p,200) if trend else True); w=concentrate_positive(raw,k,mask); out.append((f"{name}__daily_residual_r{lookback}__top{k}__trend{int(trend)}",p,w))
 elif cid in ("CAM-0611","CAM-0612"):
  cfg=((10,30),(20,50),(50,200)) if cid=="CAM-0611" else ((3,10,21),(5,20,50),(10,50,200))
  for name in ("sp500","qqq"):
   p=panels[name]
   for wins in cfg:
    mas=[moving_average(p,x) for x in wins]; condition=(mas[0]>mas[1]) if len(wins)==2 else ((mas[0]>mas[1])&(mas[1]>mas[2]))
    for k in (3,5,10):
     for score in ("momentum","risk_adjusted"):
      w=active_trend_rank(p,condition,np.arange(p.n_dates),k,score); out.append((f"{name}__ma{'_'.join(map(str,wins))}__daily__top{k}__{score}",p,w))
 elif cid=="CAM-0617":
  for name in ("qqq","etf"):
   p=panels[name]
   for history,forecast in ((20,5),(60,5),(60,20),(120,20)):
    raw,rep=alpha_combo_weights(p,history,forecast)
    for k in ((3,10) if name=="qqq" else (1,3,5)):
     for trend in (False,True):
      mask=liquid_mask(p,.5)&(trend_mask(p,200) if trend else True); w=concentrate_positive(raw,k,mask); out.append((f"{name}__alpha_M{history}_E{forecast}__daily__top{k}__trend{int(trend)}",p,w))
 return out

def main():
 panels=load_panels(); f,_=_load_or_build_fundamentals(panels)
 for cid in IDS:
  outdir=CAMPAIGNS/cid/"artifacts"/"RUN-0024"; outdir.mkdir(parents=True,exist_ok=True); rows=[]
  for vid,p,w in variants(cid,panels,f):
   for cost in COSTS_BPS:
    m,d,mo,y,s=evaluate_weights(p,w,cost,holding="open_to_next_open",execution_lag=1); rec={"campaign_id":cid,"run_id":"RUN-0024","variant_id":vid,**m,"holding":"open_to_next_open"}; rows.append(rec); save_variant(outdir,f"{vid}__cost_{cost:g}bps",rec,d,mo,y,s,save_detail=float(cost)==2)
  frame=pd.DataFrame(rows); frame.to_csv(outdir/"variant_metrics.csv",index=False); at2=frame[frame.cost_bps_per_side==2]; at10=frame[frame.cost_bps_per_side==10][["variant_id","net_simple_return"]].rename(columns={"net_simple_return":"net10"}); q=at2.merge(at10,on="variant_id"); q=q[(q.net10>0)&(q.recent12_average_month>0)&(q.recent12_positive_months>=7)].sort_values(["recent12_positive_months","recent12_average_month","maximum_drawdown"],ascending=[False,False,True]); best=None if q.empty else q.iloc[0].to_dict(); report={"status":"completed","run_id":"RUN-0024","variant_cost_tests":len(frame),"structured_candidates":len(q),"selected":best,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin":False}
  (outdir/"execution_report.json").write_text(json.dumps(report,indent=2,default=str)+"\n"); rp=CAMPAIGNS/cid/"runs"/"RUN-0024.yaml"; r=yaml.safe_load(rp.read_text()); r["status"]="completed"; r["result"]=report; r["decision"]="Quote replay only if the selected daily candidate meets exact trade-session cadence after ledger reconstruction."; rp.write_text(yaml.safe_dump(r,sort_keys=False)); print(cid,json.dumps(best,default=str) if best else "NO_SURVIVOR",flush=True)

if __name__=="__main__": main()
