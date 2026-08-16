from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0630'/'artifacts'/'RUN-0007';sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0600'/'src'))
from suite_core import load_panels

def main():
 OUT.mkdir(parents=True,exist_ok=True);z=pd.read_parquet(ROOT/'campaigns'/'CAM-0630'/'artifacts'/'RUN-0003'/'positions_with_features.parquet');z.entry_session=pd.to_datetime(z.entry_session);z.exit_session=pd.to_datetime(z.exit_session);idx=pd.DatetimeIndex(sorted(z.exit_session.unique()));p=load_panels()['qqq']
 rets=np.full_like(p.adj_close,np.nan,dtype=float);rets[1:]=p.adj_close[1:]/p.adj_close[:-1]-1
 vol20=pd.DataFrame(rets).rolling(20,min_periods=15).std(ddof=1).to_numpy();pct126=pd.DataFrame(rets).rolling(126,min_periods=63).rank(pct=True).to_numpy()
 z['weak_rank']=z.groupby('entry_session').prior_day_return.rank(method='first');zs=[];ps=[];date_to_idx={pd.Timestamp(d):i for i,d in enumerate(p.dates)}
 for r in z.itertuples():
  i=date_to_idx[pd.Timestamp(r.entry_session)];c=p.symbol_to_col[r.symbol];zs.append(rets[i-1,c]/vol20[i-2,c]);ps.append(pct126[i-1,c])
 z['shock_z']=zs;z['shock_pct']=ps;base=z.weak_rank<=1;masks={}
 for t in [-.5,-.75,-1,-1.25,-1.5,-2]:masks[f'z_le_{t:g}']=base&(z.shock_z<=t)
 for q in [.1,.2,.3,.4]:masks[f'pct_le_{q:g}']=base&(z.shock_pct<=q)
 rows=[];years=[]
 for name,mask in masks.items():
  q=z[mask].copy();count=q.groupby('exit_session').size();q['alloc']=q.exit_session.map(1/count)
  for extra in [-1,0,1,2,5,10]:
   q['ret']=(q.exit_bid*(1-extra/10000)*q['split']+q.dividend)/(q.entry_ask*(1+extra/10000))-1;q['pnl']=q.alloc*q.ret;daily=q.groupby('exit_session').pnl.sum().reindex(idx,fill_value=0);active=daily.ne(0);eq=1+daily.cumsum();dd=eq/eq.cummax().clip(lower=1)-1;mon=daily.groupby(daily.index.to_period('M')).sum();recent=daily[daily.index>=pd.Timestamp('2025-05-01')];rm=recent.groupby(recent.index.to_period('M')).sum();yr=daily.groupby(daily.index.year).sum();pos=q.pnl.clip(lower=0).sort_values(ascending=False)
   rows.append({'variant':name,'extra_bps':extra,'net_return':daily.sum(),'maximum_drawdown':-dd.min(),'worst_month':mon.min(),'positive_months':(mon>0).sum(),'negative_months':(mon<0).sum(),'active_sessions':active.sum(),'active_fraction':active.mean(),'green_days':(daily[active]>0).sum(),'red_days':(daily[active]<0).sum(),'recent12_return':recent.sum(),'recent12_positive_months':(rm>0).sum(),'recent12_negative_months':(rm<0).sum(),'early_return':daily[daily.index<pd.Timestamp('2023-08-01')].sum(),'late_return':daily[daily.index>=pd.Timestamp('2023-08-01')].sum(),'worst_year':yr.min(),'top5_positive_trade_share':pos.head(5).sum()/pos.sum()})
   years.extend({'variant':name,'extra_bps':extra,'year':int(y),'net_pnl':v} for y,v in yr.items())
 r=pd.DataFrame(rows);r.to_csv(OUT/'metrics.csv',index=False);pd.DataFrame(years).to_csv(OUT/'yearly.csv',index=False);report={'status':'completed','variants':len(masks),'executed_cost_cells':len(r),'maximum_loaded_date':'2026-04-30','holdout_rows_loaded':0,'metrics':rows};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=lambda x:x.item() if hasattr(x,'item') else x)+'\n');print(r[r.extra_bps.isin([2,5,10])].to_string(index=False))
if __name__=='__main__':main()
