from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0630'/'artifacts'/'RUN-0006'

def main():
 OUT.mkdir(parents=True,exist_ok=True);z=pd.read_parquet(ROOT/'campaigns'/'CAM-0630'/'artifacts'/'RUN-0003'/'positions_with_features.parquet');z.entry_session=pd.to_datetime(z.entry_session);z.exit_session=pd.to_datetime(z.exit_session);idx=pd.DatetimeIndex(sorted(z.exit_session.unique()));z['weak_rank']=z.groupby('entry_session').prior_day_return.rank(method='first');z['strong_rank']=z.groupby('entry_session').prior_day_return.rank(method='first',ascending=False)
 masks={'weakest1':z.weak_rank<=1,'weakest2':z.weak_rank<=2,'strongest1':z.strong_rank<=1,'weakest1_nonpositive':(z.weak_rank<=1)&(z.prior_day_return<=0),'weakest2_nonpositive':(z.weak_rank<=2)&(z.prior_day_return<=0)}
 for t in [-.005,-.01,-.015]:masks[f'weakest1_le_{t:g}']=(z.weak_rank<=1)&(z.prior_day_return<=t)
 rows=[];years=[]
 for name,mask in masks.items():
  q=z[mask].copy();count=q.groupby('exit_session').size();q['alloc']=q.exit_session.map(1/count)
  for extra in [-1,0,1,2,5,10]:
   q['ret']=(q.exit_bid*(1-extra/10000)*q['split']+q.dividend)/(q.entry_ask*(1+extra/10000))-1;q['pnl']=q.alloc*q.ret;daily=q.groupby('exit_session').pnl.sum().reindex(idx,fill_value=0);active=daily.ne(0);eq=1+daily.cumsum();dd=eq/eq.cummax().clip(lower=1)-1;mon=daily.groupby(daily.index.to_period('M')).sum();recent=daily[daily.index>=pd.Timestamp('2025-05-01')];rm=recent.groupby(recent.index.to_period('M')).sum();yr=daily.groupby(daily.index.year).sum();pos=q.pnl.clip(lower=0).sort_values(ascending=False)
   rows.append({'variant':name,'extra_bps':extra,'net_return':daily.sum(),'maximum_drawdown':-dd.min(),'worst_month':mon.min(),'positive_months':(mon>0).sum(),'negative_months':(mon<0).sum(),'active_sessions':active.sum(),'active_fraction':active.mean(),'green_days':(daily[active]>0).sum(),'red_days':(daily[active]<0).sum(),'recent12_return':recent.sum(),'recent12_positive_months':(rm>0).sum(),'recent12_negative_months':(rm<0).sum(),'early_return':daily[daily.index<pd.Timestamp('2023-08-01')].sum(),'late_return':daily[daily.index>=pd.Timestamp('2023-08-01')].sum(),'worst_year':yr.min(),'top5_positive_trade_share':pos.head(5).sum()/pos.sum()})
   years.extend({'variant':name,'extra_bps':extra,'year':int(y),'net_pnl':v} for y,v in yr.items())
 r=pd.DataFrame(rows);r.to_csv(OUT/'metrics.csv',index=False);pd.DataFrame(years).to_csv(OUT/'yearly.csv',index=False);report={'status':'completed','variants':len(masks),'executed_cost_cells':len(r),'maximum_loaded_date':'2026-04-30','holdout_rows_loaded':0,'metrics':rows};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=lambda x:x.item() if hasattr(x,'item') else x)+'\n');print(r[r.extra_bps.isin([2,5,10])].to_string(index=False))
if __name__=='__main__':main()
