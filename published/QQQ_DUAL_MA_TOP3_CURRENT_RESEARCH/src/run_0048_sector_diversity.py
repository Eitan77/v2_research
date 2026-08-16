from __future__ import annotations
import argparse,json,sys
from datetime import datetime,time
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"))
from baseline_strategies import SECTOR_ETFS,close_returns,eligible,moving_average
from deep_strategies import liquid_mask
from suite_core import evaluate_weights,forward_fill_signal_weights,load_panels,trailing_return,weekly_indices
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0048";AVG_COST=9.740340418
def build_weights(p,etf,cap):
 score=trailing_return(p,126,21);mask=eligible(p)&(moving_average(p,50)>moving_average(p,200))&liquid_mask(p,.5);ret=close_returns(p);er=close_returns(etf);ed={pd.Timestamp(d):i for i,d in enumerate(etf.dates)};ec=[etf.symbol_to_col[s] for s in SECTOR_ETFS if s in etf.symbol_to_col];w=np.zeros_like(p.adj_close);records=[]
 # Align sector returns once, then use vectorized pairwise correlations at each signal.
 aligned=np.full((len(p.dates),len(ec)),np.nan)
 for j,d in enumerate(p.dates):
  k=ed.get(pd.Timestamp(d))
  if k is not None:aligned[j]=er[k,ec]
 for i in weekly_indices(p.dates):
  if i<126:continue
  candidates=np.flatnonzero(mask[i]&np.isfinite(score[i]));clusters={}
  x=aligned[i-125:i+1];y=ret[i-125:i+1,candidates]
  for z in range(x.shape[1]):
   xv=x[:,z,None];valid=np.isfinite(xv)&np.isfinite(y);n=valid.sum(0)
   xx=np.where(valid,xv,0);yy=np.where(valid,y,0);mx=xx.sum(0)/np.maximum(n,1);my=yy.sum(0)/np.maximum(n,1)
   dx=np.where(valid,xv-mx,0);dy=np.where(valid,y-my,0);den=np.sqrt((dx*dx).sum(0)*(dy*dy).sum(0));corr=np.where((n>=101)&(den>0),(dx*dy).sum(0)/den,np.nan)
   for c,q in zip(candidates,corr):
    if np.isfinite(q) and (c not in clusters or q>clusters[c][1]):clusters[int(c)]=(z,float(q))
  chosen=[];counts={}
  for c in candidates[np.argsort(score[i,candidates])[::-1]]:
   z=clusters.get(int(c),(-1,np.nan))[0]
   if z<0 or counts.get(z,0)>=cap:continue
   chosen.append(int(c));counts[z]=counts.get(z,0)+1
   if len(chosen)==3:break
  if chosen:w[i,chosen]=1/len(chosen)
  records.append({"signal_date":str(pd.Timestamp(p.dates[i]).date()),"cap":cap,"selected":[str(p.symbols[c]) for c in chosen],"clusters":[int(clusters[c][0]) for c in chosen],"gross":float(w[i].sum())})
 return forward_fill_signal_weights(w,weekly_indices(p.dates)),records
def bars():
 OUT.mkdir(parents=True,exist_ok=True);ps=load_panels();p=ps["qqq"];etf=ps["etf"]
 if pd.Timestamp(p.dates.max())!=pd.Timestamp("2026-04-30") or pd.Timestamp(etf.dates.max())>pd.Timestamp("2026-04-30"):raise RuntimeError("holdout boundary")
 rows=[];allrec={}
 for name,cap in (("control",3),("max2",2),("max1",1)):
  w,rec=build_weights(p,etf,cap);allrec[name]=rec;m,d,*_=evaluate_weights(p,w,AVG_COST,holding="open_to_next_open",execution_lag=1);monthly=d.net_pnl.groupby(d.index.to_period("M")).sum();recent=d.net_pnl[d.index>=pd.Timestamp("2025-05-01")];rows.append({"variant":name,"net_return":float(d.net_pnl.sum()),"max_drawdown":float(m["maximum_drawdown"]),"recent12":float(recent.sum()),"positive_months":int((monthly>0).sum()),"negative_months":int((monthly<0).sum()),"worst_month":float(monthly.min()),"turnover":float(m["total_turnover"]),"underfilled_signals":sum(r["gross"]<.999 for r in rec)});np.save(OUT/f"weights_{name}.npy",w);d.reset_index().to_parquet(OUT/f"bar_daily_{name}.parquet",index=False)
 (OUT/"selection_records.json").write_text(json.dumps(allrec)+"\n");report={"status":"completed_bar_stage","cluster_rule":"weekly trailing126 highest sector ETF correlation","metrics":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0};(OUT/"bar_report.json").write_text(json.dumps(report,indent=2)+"\n");print(pd.DataFrame(rows).to_string(index=False))
def ledgers():
 p=load_panels()["qqq"];ny=ZoneInfo("America/New_York");rows=[]
 for name in ("control","max2","max1"):
  w=np.load(OUT/f"weights_{name}.npy");ex=np.zeros_like(w);ex[1:]=w[:-1];ex=np.where(np.isfinite(p.adj_open),ex,0);prev=np.zeros(p.n_symbols)
  for i,d in enumerate(p.dates):
   delta=ex[i]-prev
   for c in np.flatnonzero(np.abs(delta)>1e-12):
    symbol="AMD" if str(p.symbols[c])=="XLNX" and pd.Timestamp(d)>=pd.Timestamp("2022-02-14") else str(p.symbols[c]);side="buy" if delta[c]>0 else "sell"
    for label,clock in (("0930",(9,30)),("0940",(9,40))):rows.append({"variant":name,"label":label,"session_date":pd.Timestamp(d),"symbol":symbol,"side":side,"delta_weight":float(abs(delta[c])),"target_ts":pd.Timestamp(datetime.combine(pd.Timestamp(d).date(),time(*clock),tzinfo=ny)).tz_convert("UTC"),"role":"entry_ask_after" if side=="buy" else "exit_bid_after"})
   prev=ex[i].copy()
 l=pd.DataFrame(rows);l.to_parquet(OUT/"quote_ledger.parquet",index=False)
 for label,g in l.groupby("label"):g[["symbol","target_ts","role"]].drop_duplicates().to_parquet(OUT/f"roles_{label}.parquet",index=False)
 print({"ledger_rows":len(l),"roles":{z:len(pd.read_parquet(OUT/f'roles_{z}.parquet')) for z in ('0930','0940')}})
if __name__=="__main__":ap=argparse.ArgumentParser();ap.add_argument("phase",choices=("bars","ledgers"),nargs="?",default="bars");a=ap.parse_args();globals()[a.phase]()
