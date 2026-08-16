from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"));sys.path.insert(0,str(Path(__file__).parent))
from baseline_strategies import eligible,moving_average
from deep_strategies import liquid_mask
from suite_core import evaluate_weights,load_panels,trailing_return
from run_0027_rank_challengers import select_equal
from run_0033_exit_overlays import summary
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0038";COST=9.740340417752536;NAMES=("monday","tuesday","wednesday","thursday","friday")
def signals(dates,target):
 out=[]
 frame=pd.DataFrame({"i":np.arange(len(dates)),"date":dates});frame["week"]=dates.to_period("W-FRI")
 for _,g in frame.groupby("week",sort=True):
  if target==4:out.append(int(g.i.iloc[-1]));continue
  z=g[g.date.dt.dayofweek>=target];out.append(int((z if len(z) else g).i.iloc[0 if len(z) else -1]))
 return np.asarray(out,int)
def main():
 OUT.mkdir(parents=True,exist_ok=True);p=load_panels()["qqq"]
 if str(p.dates.max().date())!="2026-04-30" or p.readiness.get("holdout_rows_loaded_total",0)!=0:raise RuntimeError("readiness")
 score=trailing_return(p,126,21);mask=eligible(p)&(moving_average(p,50)>moving_average(p,200))&liquid_mask(p,.5);rows=[]
 for target,name in enumerate(NAMES):
  sig=signals(p.dates,target);w=select_equal(score,mask,sig,3);m,d,*_=evaluate_weights(p,w,COST,holding="open_to_next_open",execution_lag=1);rows.append({"variant":name,**summary(d.net_pnl),"turnover":float(m["total_turnover"]),"trade_sessions":int((d.turnover>1e-12).sum()),"signal_count":len(sig),"average_utilization":float(w.sum(1).mean())});np.save(OUT/f"weights_{name}.npy",w);d.reset_index().to_parquet(OUT/f"bar_daily_{name}.parquet",index=False)
 report={"status":"completed_bar_stage","planned_variants":5,"executed_variants":len(rows),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"metrics":rows};(OUT/"bar_report.json").write_text(json.dumps(report,indent=2)+"\n");print(pd.DataFrame(rows).to_string(index=False))
if __name__=="__main__":main()
