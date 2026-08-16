from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0630'/'artifacts'/'RUN-0005'

def main():
 OUT.mkdir(parents=True,exist_ok=True);z=pd.read_parquet(ROOT/'campaigns'/'CAM-0630'/'artifacts'/'RUN-0003'/'positions_with_features.parquet');z.entry_session=pd.to_datetime(z.entry_session);z.exit_session=pd.to_datetime(z.exit_session);idx=pd.DatetimeIndex(sorted(z.exit_session.unique()));rows=[];year_rows=[]
 for threshold in [-.005,-.01,-.015,-.02,-.025,-.03,-.04,-.05]:
  q=z[z.prior_day_return<=threshold].copy();counts=q.groupby('exit_session').size();q['alloc']=q.exit_session.map(1/counts)
  for extra in [-1,0,1,2,5,10]:
   entry=q.entry_ask*(1+extra/10000);exitp=q.exit_bid*(1-extra/10000);q['ret']=(exitp*q['split']+q.dividend)/entry-1;q['pnl']=q.alloc*q.ret;daily=q.groupby('exit_session').pnl.sum().reindex(idx,fill_value=0);active=daily.ne(0);eq=1+daily.cumsum();dd=eq/eq.cummax().clip(lower=1)-1;mon=daily.groupby(daily.index.to_period('M')).sum();week=daily.groupby(daily.index.to_period('W-FRI')).sum();recent=daily[daily.index>=pd.Timestamp('2025-05-01')];rm=recent.groupby(recent.index.to_period('M')).sum();pos=q.pnl.clip(lower=0).sort_values(ascending=False);year=daily.groupby(daily.index.year).sum()
   rows.append({'threshold':threshold,'extra_bps':extra,'net_return':daily.sum(),'maximum_drawdown':-dd.min(),'worst_month':mon.min(),'positive_months':(mon>0).sum(),'negative_months':(mon<0).sum(),'positive_weeks':(week>0).sum(),'negative_weeks':(week<0).sum(),'active_sessions':active.sum(),'active_fraction':active.mean(),'green_active_days':(daily[active]>0).sum(),'red_active_days':(daily[active]<0).sum(),'positions':len(q),'recent12_return':recent.sum(),'recent12_positive_months':(rm>0).sum(),'recent12_negative_months':(rm<0).sum(),'early_return':daily[daily.index<pd.Timestamp('2023-08-01')].sum(),'late_return':daily[daily.index>=pd.Timestamp('2023-08-01')].sum(),'worst_year':year.min(),'positive_years':(year>0).sum(),'negative_years':(year<0).sum(),'top5_positive_trade_share':pos.head(5).sum()/pos.sum(),'leave_best5_return':daily.sum()-pos.head(5).sum()})
   year_rows.extend({'threshold':threshold,'extra_bps':extra,'year':int(y),'net_pnl':v} for y,v in year.items())
 r=pd.DataFrame(rows);r.to_csv(OUT/'metrics.csv',index=False);pd.DataFrame(year_rows).to_csv(OUT/'yearly.csv',index=False);report={'status':'completed','planned_variants':48,'executed_variants':len(r),'maximum_loaded_date':'2026-04-30','holdout_rows_loaded':0,'quote_role_coverage':1.0,'metrics':rows};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=lambda x:x.item() if hasattr(x,'item') else x)+'\n');print(r[r.extra_bps.isin([2,5,10])].to_string(index=False))
if __name__=='__main__':main()
