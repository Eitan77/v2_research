from __future__ import annotations
import json,sys
from pathlib import Path
import duckdb,numpy as np,pandas as pd

ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0630'/'artifacts'/'RUN-0003';sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0600'/'src'))
from baseline_strategies import moving_average
from suite_core import load_panels,trailing_return

def qqq_entry_returns():
 cache=OUT/'qqq_entry_clock.parquet'
 if cache.exists():return pd.read_parquet(cache)
 con=duckdb.connect();path='D:/AlgoResearch/data/derived/alpaca/market/stocks/bars_10m/symbol=QQQ/*.parquet'
 x=con.execute(f'''with d as (select *,row_number() over(partition by timestamp,timeframe,feed,adjustment order by coalesce(try_cast(ingested_at as timestamp),timestamp '1900-01-01') desc,coalesce(source_ingestion_id,'') desc) rn from read_parquet('{path}',union_by_name=true,hive_partitioning=false) where feed='sip' and adjustment='raw' and try_cast(session_date as date)<=date '2026-04-30'), b as (select try_cast(session_date as date) date,open,close,bar_start_ts,bar_end_ts from d where rn=1), z as (select date,first(open order by bar_start_ts) as session_open,arg_max(close,bar_end_ts) filter(where strftime(bar_end_ts at time zone 'America/New_York','%H:%M') in ('15:50','12:50')) as entry_close from b group by date) select * from z order by date''').df();con.close();x['date']=pd.to_datetime(x.date);x['qqq_open_to_entry']=x.entry_close/x.session_open-1;x.to_parquet(cache,index=False);return x

def summarize(g):
 return {'n':len(g),'net_pnl':g.pnl.sum(),'average_position_return':g.position_return.mean(),'win_rate':(g.position_return>0).mean(),'recent12_net':g.loc[g.entry_session>=pd.Timestamp('2025-05-01'),'pnl'].sum(),'early_net':g.loc[g.entry_session<pd.Timestamp('2023-08-01'),'pnl'].sum(),'late_net':g.loc[g.entry_session>=pd.Timestamp('2023-08-01'),'pnl'].sum()}

def main():
 OUT.mkdir(parents=True,exist_ok=True);p=load_panels()['qqq'];f=pd.read_parquet(ROOT/'campaigns'/'CAM-0630'/'artifacts'/'RUN-0002'/'fill_ledger.parquet');f.target_ts=pd.to_datetime(f.target_ts,utc=True)
 keys=['entry_session_index','exit_session_index','entry_session','exit_session','symbol','weight'];rows=[];score=trailing_return(p,126,21);sma50=moving_average(p,50);sma200=moving_average(p,200)
 # Reconstruct position age from the carried weekly target.
 w=np.load(ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0038'/'weights_friday.npy');held=np.zeros_like(w);held[1:]=w[:-1];age=np.zeros_like(held,dtype=int)
 for c in range(p.n_symbols):
  n=0
  for i in range(p.n_dates):
   if held[i,c]>0:n=n+1 if i and held[i-1,c]>0 else 1;age[i,c]=n
   else:n=0
 for _,g in f.groupby(keys,sort=False):
  en=g[g.endpoint.eq('entry')].iloc[0];ex=g[g.endpoint.eq('exit')].iloc[0];i=int(en.entry_session_index);j=int(en.exit_session_index);c=p.symbol_to_col[str(en.symbol)];entry_mid=(float(en.bid_price)+float(en.ask_price))/2;entry=float(en.ask_price)*1.0002;exitp=float(ex.bid_price)*.9998;split=p.split_grid[j,c] if np.isfinite(p.split_grid[j,c]) and p.split_grid[j,c]>0 else 1;div=np.nan_to_num(p.dividend_grid[j,c],nan=0.0);pr=(exitp*split+div)/entry-1
  active=np.flatnonzero(held[i]>0);order=active[np.argsort(np.nan_to_num(score[i-1,active],nan=-np.inf))[::-1]];rank=int(np.flatnonzero(order==c)[0]+1)
  rows.append({'entry_session':pd.Timestamp(en.entry_session),'exit_session':pd.Timestamp(en.exit_session),'symbol':str(en.symbol),'weight':float(en.weight),'pnl':float(en.weight)*pr,'position_return':pr,'entry_ask':float(en.ask_price),'exit_bid':float(ex.bid_price),'split':float(split),'dividend':float(div),'rank':rank,'weekday':pd.Timestamp(en.entry_session).day_name(),'age_sessions':int(age[i,c]),'stock_open_to_entry':entry_mid/p.raw_open[i,c]-1,'stock_prior_close_to_entry':entry_mid/p.raw_close[i-1,c]-1,'prior_day_return':p.adj_close[i-1,c]/p.adj_close[i-2,c]-1,'prior5_return':p.adj_close[i-1,c]/p.adj_close[i-6,c]-1,'distance_sma50':p.adj_close[i-1,c]/sma50[i-1,c]-1,'distance_sma200':p.adj_close[i-1,c]/sma200[i-1,c]-1})
 z=pd.DataFrame(rows).merge(qqq_entry_returns()[['date','qqq_open_to_entry']],left_on='entry_session',right_on='date',how='left').drop(columns='date');z['intraday_residual']=z.stock_open_to_entry-z.qqq_open_to_entry;z.to_parquet(OUT/'positions_with_features.parquet',index=False)
 out=[]
 for feature in ['rank','weekday']:
  for value,g in z.groupby(feature):out.append({'feature':feature,'bucket':str(value),**summarize(g)})
 bins={'age_sessions':[0,5,10,20,40,1000],'stock_open_to_entry':[-9,-.02,0,.02,.05,9],'stock_prior_close_to_entry':[-9,-.02,0,.02,.05,9],'intraday_residual':[-9,-.02,0,.02,.05,9],'qqq_open_to_entry':[-9,-.01,0,.01,9],'prior_day_return':[-9,-.02,0,.02,.05,9],'prior5_return':[-9,-.05,0,.05,.15,9],'distance_sma200':[-9,0,.1,.25,.5,9]}
 for feature,edges in bins.items():
  bucket=pd.cut(z[feature],edges,include_lowest=True,duplicates='drop')
  for value,g in z.groupby(bucket,observed=True):out.append({'feature':feature,'bucket':str(value),**summarize(g)})
 table=pd.DataFrame(out);table.to_csv(OUT/'diagnostics.csv',index=False);report={'status':'completed','positions':len(z),'feature_coverage':{c:float(z[c].notna().mean()) for c in bins},'maximum_loaded_date':'2026-04-30','holdout_rows_loaded':0};(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(table.to_string(index=False));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
