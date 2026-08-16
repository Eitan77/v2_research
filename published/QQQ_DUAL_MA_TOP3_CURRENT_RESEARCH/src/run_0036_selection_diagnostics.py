from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"));sys.path.insert(0,str(Path(__file__).parent))
from baseline_strategies import eligible,moving_average
from deep_strategies import liquid_mask
from suite_core import evaluate_weights,forward_fill_signal_weights,load_panels,trailing_return,weekly_indices
from run_0034_staircase_ratchet import ratchet
from run_0033_exit_overlays import summary
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0036";COST=9.740340417752536
def pct(a,mask):
 out=np.full_like(a,np.nan)
 for i in range(len(a)):
  c=np.flatnonzero(mask[i]&np.isfinite(a[i]));
  if len(c):
   o=c[np.argsort(a[i,c],kind="stable")];out[i,o]=np.arange(1,len(o)+1)/len(o)
 return out
def select(score,mask,sig):
 raw=np.zeros_like(score)
 for i in sig:
  c=np.flatnonzero(mask[i]&np.isfinite(score[i]));chosen=c[np.argsort(score[i,c],kind="stable")[-min(3,len(c)):]] if len(c) else []
  if len(chosen):raw[i,chosen]=1/len(chosen)
 return forward_fill_signal_weights(raw,sig)
def main():
 OUT.mkdir(parents=True,exist_ok=True);p=load_panels()["qqq"]
 if str(p.dates.max().date())!="2026-04-30" or p.readiness.get("holdout_rows_loaded_total",0)!=0:raise RuntimeError("readiness")
 sig=weekly_indices(p.dates);mom=trailing_return(p,126,21);s50=moving_average(p,50);s200=moving_average(p,200);base=eligible(p)&(s50>s200)&liquid_mask(p,.5);dv=pd.DataFrame(p.raw_close*p.volume).rolling(63,min_periods=32).median().to_numpy();v20=pd.DataFrame(p.volume).rolling(20,min_periods=20).median().to_numpy();rv=np.divide(p.volume,v20,out=np.full_like(p.volume,np.nan),where=np.isfinite(v20)&(v20>0));dist=np.divide(p.adj_close,s200,out=np.full_like(p.adj_close,np.nan),where=np.isfinite(s200)&(s200>0))-1;mp=pct(mom,base);lp=pct(dv,base);dp=pct(dist,base)
 cfg={"control":(mom,base),"mom_floor25":(mom,base&(mom>=.25)),"mom_floor50":(mom,base&(mom>=.50)),"dist200_floor10":(mom,base&(dist>=.10)),"dist200_floor20":(mom,base&(dist>=.20)),"liquidity_top25":(mom,eligible(p)&(s50>s200)&liquid_mask(p,.25)),"quiet_volume_le1":(mom,base&(rv<=1)),"demand_volume_ge1":(mom,base&(rv>=1)),"blend_mom75_liq25":(.75*mp+.25*lp,base),"blend_mom75_dist25":(.75*mp+.25*dp,base)};rows=[]
 for name,(score,mask) in cfg.items():
  normal=select(score,mask,sig)
  for exit_name,w in (("normal",normal),("stair15_5_2close",ratchet(p,sig,normal,(.15,.05,2))[0])):
   m,d,*_=evaluate_weights(p,w,COST,holding="open_to_next_open",execution_lag=1);rows.append({"variant":name,"exit":exit_name,**summary(d.net_pnl),"turnover":float(m["total_turnover"]),"trade_sessions":int((d.turnover>1e-12).sum()),"average_utilization":float(w.sum(1).mean()),"active_signal_dates":int((w[sig].sum(1)>0).sum()),"eligible_cells":int(mask[sig].sum())});np.save(OUT/f"weights_{name}_{exit_name}.npy",w)
 report={"status":"completed_bar_stage","planned_variants":20,"executed_variants":len(rows),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"metrics":rows};(OUT/"bar_report.json").write_text(json.dumps(report,indent=2)+"\n");print(pd.DataFrame(rows).sort_values("net_simple_return",ascending=False).to_string(index=False))
if __name__=="__main__":main()
