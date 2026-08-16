from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0600'/'src'))
from suite_core import load_panels
OUT=ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0052';A=ROOT/'campaigns'/'CAM-0611'/'artifacts';COST=9.740340418/10000
def main():
 OUT.mkdir(parents=True,exist_ok=True);ps=load_panels();p=ps['qqq'];etf=ps['etf'];daily=pd.read_parquet(A/'RUN-0042'/'daily_all_adjusted_apr_augtd.parquet');daily.date=pd.to_datetime(daily.date);dates=pd.DatetimeIndex(pd.read_parquet(A/'RUN-0041'/'gross_daily.parquet').date);gross=pd.read_parquet(A/'RUN-0041'/'gross_daily.parquet').gross_pnl.to_numpy();w=np.load(A/'RUN-0041'/'executed_weights.npy');targets=json.loads((A/'RUN-0041'/'targets.json').read_text())
 ext=pd.DatetimeIndex(sorted(daily[daily.date.between('2026-05-01','2026-07-31')].date.unique()));all_dates=pd.DatetimeIndex(np.r_[p.dates,ext]);close={}
 for s in set(sum([x['selected'] for x in targets],[]))|{'QQQ'}:
  if s=='QQQ':hist=pd.Series(etf.adj_close[:,etf.symbol_to_col['QQQ']],index=etf.dates).reindex(p.dates).ffill()
  elif s in p.symbol_to_col:hist=pd.Series(p.adj_close[:,p.symbol_to_col[s]],index=p.dates)
  else:hist=pd.Series(index=p.dates,dtype=float)
  g=daily[daily.symbol.eq(s)].set_index('date').close.reindex(ext);anchor=daily[(daily.symbol.eq(s))&daily.date.eq('2026-04-30')]
  scale=(float(hist.dropna().iloc[-1])/float(anchor.close.iloc[0])) if s!='QQQ' and len(anchor) and len(hist.dropna()) else 1.;close[s]=pd.concat([hist,g*scale]).reindex(all_dates).ffill()
 q=close['QQQ'];qr=q.pct_change();q20=q.rolling(20).mean();v20=qr.rolling(20).std();v126=qr.rolling(126).std();signals=[]
 for t in targets:
  d=pd.Timestamp(t['signal_date']);names=t['selected'];leader=sum(float(close[s].loc[d])>float(close[s].rolling(20).mean().loc[d]) for s in names)>=2;market=float(q.loc[d])>float(q20.loc[d]);scale=min(1.,float(v126.loc[d]/v20.loc[d])) if np.isfinite(v126.loc[d]) and np.isfinite(v20.loc[d]) and v20.loc[d]>0 else 0.;signals.append((d,scale,1. if market and leader else 0.))
 scalars={'baseline':np.ones(len(dates)),'volatility_ratio_scaling':np.zeros(len(dates)),'qqq_and_leaders':np.zeros(len(dates))}
 for i,d in enumerate(dates):
  prior=[x for x in signals if x[0]<d]
  if prior:scalars['volatility_ratio_scaling'][i]=prior[-1][1];scalars['qqq_and_leaders'][i]=prior[-1][2]
 rows=[]
 for name,s in scalars.items():
  x=w*s[:,None];turn=np.abs(np.diff(x,axis=0,prepend=np.zeros((1,x.shape[1])))).sum(1);net=gross*s-turn*COST;sel=(dates>=pd.Timestamp('2026-07-01'))&(dates<=pd.Timestamp('2026-07-31'));z=net[sel];eq=1+np.cumsum(z);peak=np.maximum.accumulate(np.r_[1.,eq])[1:];rows.append({'variant':name,'july_return_pct':100*z.sum(),'july_max_dd_pct':-100*(eq/peak-1).min(),'active_sessions':int((s[sel]>0).sum()),'average_exposure':float(s[sel].mean()),'turnover':float(turn[sel].sum())})
 report={'status':'completed_bar_diagnostic','warning':'July known before overlays; not clean OOS','maximum_loaded_date':'2026-07-31','rows_after_authorized_end':0,'metrics':rows};(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(pd.DataFrame(rows).to_string(index=False))
if __name__=='__main__':main()
