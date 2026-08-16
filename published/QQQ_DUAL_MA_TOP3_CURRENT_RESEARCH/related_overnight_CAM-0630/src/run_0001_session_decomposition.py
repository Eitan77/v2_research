from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd

ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0630'/'artifacts'/'RUN-0001'
sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0600'/'src'))
from suite_core import load_panels

def metrics(net,active):
 eq=1+net.cumsum();peak=eq.cummax().clip(lower=1);dd=eq/peak-1;mon=net.groupby(net.index.to_period('M')).sum();recent=net[net.index>=pd.Timestamp('2025-05-01')]
 split=pd.Timestamp('2023-08-01');pos=net.clip(lower=0).sort_values(ascending=False)
 return {'net_return':float(net.sum()),'maximum_drawdown':float(-dd.min()),'worst_month':float(mon.min()),'positive_months':int((mon>0).sum()),'negative_months':int((mon<0).sum()),'active_sessions':int(active.sum()),'green_sessions':int((net[active]>0).sum()),'red_sessions':int((net[active]<0).sum()),'mean_active_day':float(net[active].mean()),'recent12_return':float(recent.sum()),'early_return':float(net[net.index<split].sum()),'late_return':float(net[net.index>=split].sum()),'top5_positive_day_share':float(pos.head(5).sum()/pos.sum()) if pos.sum()>0 else None}

def main():
 OUT.mkdir(parents=True,exist_ok=True);p=load_panels()['qqq']
 if str(pd.Timestamp(p.dates.max()).date())!='2026-04-30' or int(p.readiness.get('holdout_rows_loaded_total',0))!=0:raise RuntimeError('readiness failure')
 selectors={'weekly_carried':np.load(ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0038'/'weights_friday.npy'),'daily_refreshed':np.load(ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0030'/'weights_daily_rebalance.npy')}
 dates=pd.DatetimeIndex(p.dates);rows=[];daily_rows=[]
 dividend=np.nan_to_num(p.dividend_grid*p.split_factor,nan=0.0)
 overnight=np.zeros_like(p.adj_close);valid=(p.adj_close[:-1]>0)&np.isfinite(p.adj_open[1:])&np.isfinite(p.adj_close[:-1]);overnight[:-1]=np.where(valid,(p.adj_open[1:]+dividend[1:])/p.adj_close[:-1]-1,0.0)
 intraday=np.nan_to_num(p.open_to_close_return,nan=0.0)
 for selector,w in selectors.items():
  # One-session lag makes the completed prior close the information boundary.
  held=np.zeros_like(w);held[1:]=w[:-1];held=np.where(np.isfinite(p.adj_open),held,0.0)
  for leg,ret in [('intraday',intraday),('overnight',overnight)]:
   gross=(held*ret).sum(axis=1);active=np.abs(held).sum(axis=1)>1e-12
   for bps in (-1,0,1,2,5,10):
    # Every session is a flat-to-long-to-flat round trip.
    net=pd.Series(gross-active.astype(float)*2*bps/10000,index=dates)
    m=metrics(net,pd.Series(active,index=dates));rows.append({'selector':selector,'leg':leg,'bps_per_side':bps,**m})
    if bps==2:
     daily_rows.extend({'date':d,'selector':selector,'leg':leg,'gross_pnl':float(g),'net_pnl':float(n),'active':bool(a)} for d,g,n,a in zip(dates,gross,net,active))
 result=pd.DataFrame(rows);result.to_csv(OUT/'metrics.csv',index=False);pd.DataFrame(daily_rows).to_parquet(OUT/'daily_2bps.parquet',index=False)
 report={'status':'completed','planned_variants':24,'executed_variants':len(result),'maximum_loaded_date':'2026-04-30','holdout_rows_loaded':0,'broker_margin':False,'metrics':rows};(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 print(result[result.bps_per_side.isin([0,2,10])].to_string(index=False))
if __name__=='__main__':main()
