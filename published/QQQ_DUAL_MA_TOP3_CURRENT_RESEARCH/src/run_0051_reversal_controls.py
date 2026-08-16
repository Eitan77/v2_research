from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0600'/'src'));sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0611'/'src'))
from baseline_strategies import eligible,moving_average
from deep_strategies import liquid_mask
from suite_core import evaluate_weights,forward_fill_signal_weights,load_panels,trailing_return,weekly_indices
OUT=ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0051';COST=9.740340418
def main():
 OUT.mkdir(parents=True,exist_ok=True);ps=load_panels();p=ps['qqq'];e=ps['etf'];sig=weekly_indices(p.dates);base=np.load(ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0038'/'weights_friday.npy');s20=moving_average(p,20);member=eligible(p);breadth=np.nanmean(np.where(member,p.adj_close>s20,np.nan),axis=1)
 q=e.adj_close[:,e.symbol_to_col['QQQ']];qd={pd.Timestamp(d):v for d,v in zip(e.dates,q)};qa=pd.Series([qd.get(pd.Timestamp(d),np.nan) for d in p.dates]).ffill().to_numpy();qs20=pd.Series(qa).rolling(20,min_periods=20).mean().to_numpy();qr=pd.Series(qa).pct_change();v20=qr.rolling(20).std().to_numpy();v126=qr.rolling(126).std().to_numpy()
 raw={k:np.zeros_like(base) for k in ('breadth_drop_10pp','leaders_2of3_above_sma20','qqq_above_sma20','qqq_and_leaders','volatility_ratio_scaling')}
 for i in sig:
  chosen=np.flatnonzero(base[i]>0);leader=(len(chosen)==3 and int(np.sum(p.adj_close[i,chosen]>s20[i,chosen]))>=2);market=np.isfinite(qa[i]) and qa[i]>qs20[i]
  raw['breadth_drop_10pp'][i]=base[i] if i>=5 and breadth[i]-breadth[i-5]>=-.10 else 0;raw['leaders_2of3_above_sma20'][i]=base[i] if leader else 0;raw['qqq_above_sma20'][i]=base[i] if market else 0;raw['qqq_and_leaders'][i]=base[i] if market and leader else 0
  scale=min(1.,v126[i]/v20[i]) if np.isfinite(v20[i]) and v20[i]>0 and np.isfinite(v126[i]) else 0;raw['volatility_ratio_scaling'][i]=base[i]*scale
 variants={'baseline':base};variants.update({k:forward_fill_signal_weights(v,sig) for k,v in raw.items()})
 # Replace only unusually accelerated names, preserving breadth and equal weights.
 score=trailing_return(p,126,21);r5=pd.DataFrame(trailing_return(p,5,0));hist=r5.shift(1);rz=((r5-hist.rolling(125,min_periods=79).mean())/hist.rolling(125,min_periods=79).std(ddof=0)).to_numpy();mask=member&(moving_average(p,50)>moving_average(p,200))&liquid_mask(p,.5);w=np.zeros_like(base)
 for i in sig:
  c=np.flatnonzero(mask[i]&np.isfinite(score[i])&np.isfinite(rz[i])&(rz[i]<=2));pick=c[np.argsort(score[i,c])[-min(3,len(c)):]];w[i,pick]=1/len(pick) if len(pick) else 0
 variants['acceleration_replace_2sigma']=forward_fill_signal_weights(w,sig);rows=[]
 for name,x in variants.items():
  m,d,*_=evaluate_weights(p,x,COST,holding='open_to_next_open',execution_lag=1);z=d[d.index.to_series().between('2025-05-01','2026-04-30')];mon=z.net_pnl.groupby(z.index.to_period('M')).sum();eq=1+z.net_pnl.cumsum();peak=np.maximum.accumulate(np.r_[1.,eq.to_numpy()])[1:];rows.append({'variant':name,'return_pct':100*z.net_pnl.sum(),'max_dd_pct':-100*(eq/peak-1).min(),'worst_month_pct':100*mon.min(),'positive_months':int((mon>0).sum()),'active_sessions':int((z.gross_exposure>0).sum())})
  if name=='qqq_and_leaders':d.reset_index().to_parquet(OUT/'daily_qqq_and_leaders.parquet',index=False);np.save(OUT/'weights_qqq_and_leaders.npy',x)
 report={'status':'completed_bar_stage','maximum_loaded_date':'2026-04-30','holdout_rows_loaded':0,'metrics':rows};(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(pd.DataFrame(rows).to_string(index=False))
if __name__=='__main__':main()
