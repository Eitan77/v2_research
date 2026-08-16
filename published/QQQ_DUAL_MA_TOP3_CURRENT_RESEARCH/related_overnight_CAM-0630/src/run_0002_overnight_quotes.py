from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np,pandas as pd

ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0630'/'artifacts'/'RUN-0002'
sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0600'/'src'))
import replay_sma_session_quotes as shared
from suite_core import load_panels
NAME='qqq_dual_ma50_200_weekly_top3_f126_s21__close_to_next_open_daily_cash_reset'
EARLY_CLOSE={pd.Timestamp(x).date() for x in ['2020-12-24','2021-11-26','2022-11-25','2023-11-24','2024-07-03','2024-12-24','2025-07-03','2025-11-28','2025-12-24']}

def ledger():
 OUT.mkdir(parents=True,exist_ok=True);src=pd.read_parquet(ROOT/'campaigns'/'CAM-0600'/'artifacts'/'RUN-0047'/'position_role_ledger.parquet');x=src[src.candidate.eq(NAME)].copy()
 local=pd.to_datetime(x.target_ts,utc=True).dt.tz_convert('America/New_York');short=x.endpoint.eq('entry')&local.dt.date.isin(EARLY_CLOSE)
 x.loc[short,'target_ts']=[pd.Timestamp(f'{d} 12:50',tz='America/New_York').tz_convert('UTC') for d in pd.to_datetime(x.loc[short,'entry_session']).dt.date]
 x.loc[short,'clock']='1250'
 if len(x)!=9120 or (pd.to_datetime(x.target_ts,utc=True)>=pd.Timestamp('2026-05-01',tz='UTC')).any():raise RuntimeError('ledger fixture failure')
 x.to_parquet(OUT/'position_role_ledger.parquet',index=False)
 for clock,g in x.groupby('clock'):
  roles=g[['symbol','target_ts','role']].drop_duplicates();roles.to_parquet(OUT/f'roles_{clock}.parquet',index=False)
  q=shared.quote_cache(clock);roles.target_ts=pd.to_datetime(roles.target_ts,utc=True);m=roles.merge(q[['symbol','target_ts','role']],on=['symbol','target_ts','role'],how='left',indicator=True);missing=m[m._merge.eq('left_only')][['symbol','target_ts','role']];missing.to_parquet(OUT/f'missing_{clock}.parquet',index=False);print(clock,'roles',len(roles),'missing',len(missing))

def replay():
 p=load_panels()['qqq'];led=pd.read_parquet(OUT/'position_role_ledger.parquet');led.target_ts=pd.to_datetime(led.target_ts,utc=True);filled=[]
 for clock,g in led.groupby('clock'):
  q=shared.quote_cache(clock);filled.append(g.merge(q[['symbol','target_ts','role','quote_ts','bid_price','ask_price']],on=['symbol','target_ts','role'],how='left',validate='many_to_one'))
 f=pd.concat(filled,ignore_index=True)
 terminal=f.symbol.eq('XLNX')&f.endpoint.eq('exit')&f.bid_price.isna()
 if terminal.any():
  xt=pd.read_parquet(ROOT/'campaigns'/'CAM-0600'/'artifacts'/'RUN-0042'/'xlnx_terminal_quote.parquet').iloc[0];f.loc[terminal,'bid_price']=float(xt.bid_price);f.loc[terminal,'ask_price']=float(xt.ask_price);f.loc[terminal,'quote_ts']=pd.Timestamp(xt.quote_ts)
 f['complete']=f.bid_price.gt(0)&f.ask_price.ge(f.bid_price);coverage=float(f.complete.mean());f.to_parquet(OUT/'fill_ledger.parquet',index=False)
 keys=['entry_session_index','exit_session_index','entry_session','exit_session','symbol','weight'];positions=[]
 for _,g in f.groupby(keys,sort=False):
  en=g[g.endpoint.eq('entry')].iloc[0];ex=g[g.endpoint.eq('exit')].iloc[0];positions.append({**{k:en[k] for k in keys},'entry_ask':en.ask_price,'exit_bid':ex.bid_price,'complete':bool(en.complete and ex.complete)})
 z=pd.DataFrame(positions)
 if coverage<1 or not z.complete.all() or len(z)!=4560:raise RuntimeError(f'incomplete quote replay coverage={coverage} positions={len(z)}')
 rows=[];daily_out=[];monthly_out=[];trade_out=[];cols=np.array([p.symbol_to_col[str(s)] for s in z.symbol]);ei=z.exit_session_index.astype(int).to_numpy();split=np.where(np.isfinite(p.split_grid[ei,cols])&(p.split_grid[ei,cols]>0),p.split_grid[ei,cols],1.0);div=np.nan_to_num(p.dividend_grid[ei,cols],nan=0.0)
 for extra in (-1,0,1,2,5,10):
  entry=z.entry_ask.to_numpy(float)*(1+extra/10000);exitp=z.exit_bid.to_numpy(float)*(1-extra/10000);pnl=z.weight.to_numpy(float)*((exitp*split+div)/entry-1)
  daily=pd.Series(pnl,index=pd.to_datetime(z.exit_session)).groupby(level=0).sum().reindex(pd.DatetimeIndex(p.dates),fill_value=0);active=daily.ne(0);eq=1+daily.cumsum();dd=eq/eq.cummax().clip(lower=1)-1;week=daily.groupby(daily.index.to_period('W-FRI')).sum();month=daily.groupby(daily.index.to_period('M')).sum();recent=daily[daily.index>=pd.Timestamp('2025-05-01')];rm=recent.groupby(recent.index.to_period('M')).sum();pos=pd.Series(pnl).clip(lower=0).sort_values(ascending=False)
  rows.append({'extra_bps_per_side':extra,'net_return':float(daily.sum()),'maximum_drawdown':float(-dd.min()),'positions':len(z),'active_sessions':int(active.sum()),'green_sessions':int((daily[active]>0).sum()),'red_sessions':int((daily[active]<0).sum()),'positive_weeks':int((week>0).sum()),'negative_weeks':int((week<0).sum()),'positive_months':int((month>0).sum()),'negative_months':int((month<0).sum()),'worst_month':float(month.min()),'recent12_return':float(recent.sum()),'recent12_positive_months':int((rm>0).sum()),'recent12_negative_months':int((rm<0).sum()),'early_return':float(daily[daily.index<pd.Timestamp('2023-08-01')].sum()),'late_return':float(daily[daily.index>=pd.Timestamp('2023-08-01')].sum()),'average_position_pnl':float(np.mean(pnl)),'position_win_rate':float(np.mean(pnl>0)),'top5_positive_trade_share':float(pos.head(5).sum()/pos.sum())})
  daily_out.extend({'extra':extra,'date':d,'net_pnl':v} for d,v in daily.items());monthly_out.extend({'extra':extra,'month':str(k),'net_pnl':v} for k,v in month.items());trade_out.extend({'extra':extra,'symbol':s,'entry_session':a,'exit_session':b,'pnl':v} for s,a,b,v in zip(z.symbol,z.entry_session,z.exit_session,pnl))
 pd.DataFrame(rows).to_csv(OUT/'metrics.csv',index=False);pd.DataFrame(daily_out).to_parquet(OUT/'daily.parquet',index=False);pd.DataFrame(monthly_out).to_csv(OUT/'monthly.csv',index=False);pd.DataFrame(trade_out).to_parquet(OUT/'trades.parquet',index=False)
 report={'status':'completed','quote_role_coverage':coverage,'positions':len(z),'maximum_loaded_date':'2026-04-30','holdout_rows_loaded':0,'broker_margin':False,'metrics':rows};(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(pd.DataFrame(rows).to_string(index=False))

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('phase',choices=['ledger','replay']);a=ap.parse_args();{'ledger':ledger,'replay':replay}[a.phase]()
