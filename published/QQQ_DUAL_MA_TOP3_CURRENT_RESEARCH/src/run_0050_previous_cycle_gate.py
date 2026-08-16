from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0600'/'src'))
from suite_core import evaluate_weights,load_panels,weekly_indices
OUT=ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0050';COST=9.740340418
def main():
 OUT.mkdir(parents=True,exist_ok=True);p=load_panels()['qqq'];base=np.load(ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0038'/'weights_friday.npy');shadow=pd.read_parquet(ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0038'/'bar_daily_friday.parquet');shadow.date=pd.to_datetime(shadow.date);shadow=shadow.set_index('date').reindex(pd.DatetimeIndex(p.dates))
 sig=weekly_indices(p.dates);execs=sig+1;execs=execs[execs<len(p.dates)];gate_by_exec={};cycles=[]
 for k,e in enumerate(execs):
  end=(execs[k+1]-1) if k+1<len(execs) else len(p.dates)-1
  pnl=float(shadow.net_pnl.iloc[e:end+1].sum());cycles.append({'execution_date':str(pd.Timestamp(p.dates[e]).date()),'end_date':str(pd.Timestamp(p.dates[end]).date()),'shadow_net_pnl':pnl})
  if k+1<len(execs):gate_by_exec[int(execs[k+1])]=pnl>0
 gated=base.copy()
 for k,s in enumerate(sig):
  e=int(s+1)
  if e>=len(p.dates):continue
  allow=gate_by_exec.get(e,False)
  end=(sig[k+1]-1) if k+1<len(sig) else len(p.dates)-1
  if not allow:gated[s:end+1]=0
 rows=[]
 for name,w in [('baseline',base),('previous_cycle_positive',gated)]:
  m,d,*_=evaluate_weights(p,w,COST,holding='open_to_next_open',execution_lag=1);x=d[(d.index>=pd.Timestamp('2025-05-01'))&(d.index<=pd.Timestamp('2026-04-30'))];mon=x.net_pnl.groupby(x.index.to_period('M')).sum();eq=1+x.net_pnl.cumsum();peak=np.maximum.accumulate(np.r_[1.,eq.to_numpy()])[1:];rows.append({'variant':name,'return_pct':float(100*x.net_pnl.sum()),'max_drawdown_pct':float(-100*(eq/peak-1).min()),'positive_months':int((mon>0).sum()),'negative_months':int((mon<0).sum()),'worst_month_pct':float(100*mon.min()),'active_sessions':int((x.gross_exposure>0).sum()),'turnover':float(x.turnover.sum())});d.reset_index().to_parquet(OUT/f'daily_{name}.parquet',index=False)
 report={'status':'completed_bar_stage','window':'2025-05-01 through 2026-04-30','maximum_loaded_date':'2026-04-30','holdout_rows_loaded':0,'metrics':rows,'cycle_gate_counts':{'on':sum(gate_by_exec.values()),'off':len(gate_by_exec)-sum(gate_by_exec.values())}};(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');(OUT/'shadow_cycles.json').write_text(json.dumps(cycles,indent=2)+'\n');np.save(OUT/'weights_gated.npy',gated);print(pd.DataFrame(rows).to_string(index=False));print(report['cycle_gate_counts'])
if __name__=='__main__':main()
