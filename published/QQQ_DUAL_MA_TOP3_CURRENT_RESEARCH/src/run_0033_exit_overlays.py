from __future__ import annotations
import json,sys
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"));sys.path.insert(0,str(Path(__file__).parent))
from baseline_strategies import eligible,moving_average
from deep_strategies import liquid_mask
from suite_core import evaluate_weights,load_panels,trailing_return,weekly_indices
from run_0027_rank_challengers import select_equal
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0033";COST=9.740340417752536
SPECS={
 "control":{},"stop3":{"stop":-.03},"stop5":{"stop":-.05},"stop8":{"stop":-.08},
 "tp5":{"tp":.05},"tp10":{"tp":.10},"tp15":{"tp":.15},
 "trim5":{"trim":.05},"trim10":{"trim":.10},
 "trail5_3":{"activate":.05,"trail":.03},"trail10_5":{"activate":.10,"trail":.05},
 "time5":{"time":5},"time10":{"time":10},"rank5":{"rank":5},"rank10":{"rank":10},
 "lose_sma50":{"sma50":True},"bracket5_trail10_5":{"stop":-.05,"activate":.10,"trail":.05}}
def base_context():
 p=load_panels()["qqq"]
 if str(p.dates.max().date())!="2026-04-30" or p.readiness.get("holdout_rows_loaded_total",0)!=0:raise RuntimeError("readiness failed")
 score=trailing_return(p,126,21);mask=eligible(p)&(moving_average(p,50)>moving_average(p,200))&liquid_mask(p,.5);sig=weekly_indices(p.dates);base=select_equal(score,mask,sig,3);sma50=moving_average(p,50)
 ranks=np.full_like(score,9999.0)
 for i in range(len(p.dates)):
  cols=np.flatnonzero(mask[i]&np.isfinite(score[i]));order=cols[np.argsort(score[i,cols],kind="stable")[::-1]];ranks[i,order]=np.arange(1,len(order)+1)
 return p,score,mask,sig,base,sma50,ranks
def overlay(p,sig,base,sma50,ranks,spec):
 if not spec:return base.copy(),Counter()
 decisions=np.zeros_like(base);current=np.zeros(p.n_symbols);entry=np.full(p.n_symbols,np.nan);peak=np.full(p.n_symbols,np.nan);age=np.zeros(p.n_symbols,int);trimmed=np.zeros(p.n_symbols,bool);counts=Counter();weekly=set(sig.tolist())
 for i in range(len(p.dates)):
  # Apply yesterday's decision at today's open and initialize changed positions.
  executed=np.zeros(p.n_symbols) if i==0 else decisions[i-1].copy();opened=(executed>1e-12)&(current<=1e-12);closed=(executed<=1e-12)&(current>1e-12)
  entry[opened]=p.adj_open[i,opened];peak[opened]=p.adj_close[i,opened];age[opened]=0;trimmed[opened]=False
  entry[closed]=np.nan;peak[closed]=np.nan;age[closed]=0;trimmed[closed]=False;current=executed.copy();held=current>1e-12;age[held]+=1;peak[held]=np.fmax(peak[held],p.adj_close[i,held])
  if i in weekly:
   decisions[i]=base[i];continue
  target=current.copy()
  for c in np.flatnonzero(held):
   if not np.isfinite(p.adj_close[i,c]) or not np.isfinite(entry[c]) or entry[c]<=0:continue
   ret=p.adj_close[i,c]/entry[c]-1;reason=None
   if "stop" in spec and ret<=spec["stop"]:reason="stop"
   elif "tp" in spec and ret>=spec["tp"]:reason="take_profit"
   elif "activate" in spec and peak[c]/entry[c]-1>=spec["activate"] and p.adj_close[i,c]<=peak[c]*(1-spec["trail"]):reason="trailing"
   elif "time" in spec and age[c]>=spec["time"] and ret<=0:reason="time"
   elif "rank" in spec and ranks[i,c]>spec["rank"]:reason="rank"
   elif spec.get("sma50") and np.isfinite(sma50[i,c]) and p.adj_close[i,c]<sma50[i,c]:reason="sma50"
   if reason:target[c]=0;counts[reason]+=1
   elif "trim" in spec and not trimmed[c] and ret>=spec["trim"]:target[c]*=.5;trimmed[c]=True;counts["trim"]+=1
  decisions[i]=target
 return decisions,counts
def summary(net):
 eq=1+net.cumsum();dd=(eq.cummax()-eq)/eq.cummax();mon=net.groupby(net.index.to_period("M")).sum();recent=net.loc[net.index>=pd.Timestamp("2025-05-01")];years=net.groupby(net.index.year).sum()
 return {"net_simple_return":float(net.sum()),"maximum_drawdown":float(dd.max()),"positive_months":int((mon>0).sum()),"negative_months":int((mon<0).sum()),"worst_month":float(mon.min()),"worst_year":float(years.min()),"recent12_return":float(recent.sum()),"recent12_positive_months":int((recent.groupby(recent.index.to_period('M')).sum()>0).sum())}
def main():
 OUT.mkdir(parents=True,exist_ok=True);p,score,mask,sig,base,sma50,ranks=base_context();rows=[]
 for name,spec in SPECS.items():
  w,counts=overlay(p,sig,base,sma50,ranks,spec);m,d,*_=evaluate_weights(p,w,COST,holding="open_to_next_open",execution_lag=1);rows.append({"variant":name,**summary(d.net_pnl),"turnover":float(m["total_turnover"]),"trade_sessions":int((d.turnover>1e-12).sum()),"average_utilization":float(w.sum(1).mean()),"exit_counts":dict(counts)});d.reset_index().to_parquet(OUT/f"daily_{name}.parquet",index=False);np.save(OUT/f"weights_{name}.npy",w)
 report={"status":"completed_bar_stage","executed_variants":len(rows),"planned_variants":len(SPECS),"decision_timing":"completed close, next-open execution","intraday_ordering_used":False,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"metrics":rows};(OUT/"bar_report.json").write_text(json.dumps(report,indent=2)+"\n");pd.DataFrame(rows).to_csv(OUT/"metrics.csv",index=False);print(pd.DataFrame(rows).sort_values("net_simple_return",ascending=False).to_string(index=False))
if __name__=="__main__":main()
