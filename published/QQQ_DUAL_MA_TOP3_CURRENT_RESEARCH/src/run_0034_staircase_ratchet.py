from __future__ import annotations
import json,sys
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(Path(__file__).parent));sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"))
from suite_core import evaluate_weights
from run_0033_exit_overlays import base_context,summary
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0034";COST=9.740340417752536
SPECS={"control":None,"stair10_5_1close":(.10,.05,1),"stair10_5_2close":(.10,.05,2),"stair15_5_1close":(.15,.05,1),"stair15_5_2close":(.15,.05,2),"stair20_10_1close":(.20,.10,1)}
def ratchet(p,sig,base,spec):
 if spec is None:return base.copy(),Counter(),True
 activation,step,confirm=spec;decisions=np.zeros_like(base);current=np.zeros(p.n_symbols);entry=np.full(p.n_symbols,np.nan);peakret=np.full(p.n_symbols,-np.inf);floor=np.full(p.n_symbols,-np.inf);breaches=np.zeros(p.n_symbols,int);counts=Counter();weekly=set(sig.tolist());monotonic=True
 for i in range(len(p.dates)):
  executed=np.zeros(p.n_symbols) if i==0 else decisions[i-1].copy();opened=(executed>1e-12)&(current<=1e-12);closed=(executed<=1e-12)&(current>1e-12)
  entry[opened]=p.adj_open[i,opened];peakret[opened]=-np.inf;floor[opened]=-np.inf;breaches[opened]=0
  entry[closed]=np.nan;peakret[closed]=-np.inf;floor[closed]=-np.inf;breaches[closed]=0;current=executed.copy()
  held=current>1e-12;valid=held&np.isfinite(p.adj_close[i])&np.isfinite(entry)&(entry>0);ret=np.full(p.n_symbols,np.nan);ret[valid]=p.adj_close[i,valid]/entry[valid]-1;peakret[valid]=np.maximum(peakret[valid],ret[valid]);old=floor.copy()
  active=valid&(peakret>=activation);levels=np.floor((peakret[active]-activation+1e-12)/step);floor[active]=np.maximum(floor[active],levels*step);monotonic &= bool(np.all(floor[active]>=old[active]-1e-12))
  if i in weekly:decisions[i]=base[i];breaches[:]=0;continue
  target=current.copy();below=active&(ret<=floor+1e-12);breaches[below]+=1;breaches[active&~below]=0
  fire=below&(breaches>=confirm);target[fire]=0;counts[f"ratchet_{confirm}close"]+=int(fire.sum());decisions[i]=target
 return decisions,counts,monotonic
def main():
 OUT.mkdir(parents=True,exist_ok=True);p,score,mask,sig,base,sma50,ranks=base_context();rows=[];allmono=True
 for name,spec in SPECS.items():
  w,c,mono=ratchet(p,sig,base,spec);allmono&=mono;m,d,*_=evaluate_weights(p,w,COST,holding="open_to_next_open",execution_lag=1);rows.append({"variant":name,**summary(d.net_pnl),"turnover":float(m["total_turnover"]),"trade_sessions":int((d.turnover>1e-12).sum()),"average_utilization":float(w.sum(1).mean()),"exit_counts":dict(c),"floor_monotonic":mono});np.save(OUT/f"weights_{name}.npy",w);d.reset_index().to_parquet(OUT/f"bar_daily_{name}.parquet",index=False)
 if not allmono:raise RuntimeError("ratchet floor decreased")
 report={"status":"completed_bar_stage","planned_variants":len(SPECS),"executed_variants":len(rows),"floor_monotonic":allmono,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"metrics":rows};(OUT/"bar_report.json").write_text(json.dumps(report,indent=2)+"\n");print(pd.DataFrame(rows).sort_values("net_simple_return",ascending=False).to_string(index=False))
if __name__=="__main__":main()
