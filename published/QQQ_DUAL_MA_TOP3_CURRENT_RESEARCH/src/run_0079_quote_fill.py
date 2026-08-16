from __future__ import annotations
import argparse,json,sys
from datetime import datetime,time
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path[:0]=[str(Path(__file__).parent),str(ROOT/'campaigns'/'CAM-0600'/'src')]
from baseline_strategies import eligible,moving_average
from deep_strategies import liquid_mask
from suite_core import load_panels,trailing_return,weekly_indices,evaluate_weights
from run_0027_rank_challengers import select_equal,period_metrics
import replay_sma_session_quotes as shared
OUT=ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0079';SKIPS=[0,1,5,10,15,21,30];NY=ZoneInfo('America/New_York')
def utc(day,clock):
 h,m=map(int,clock.split(':'));return pd.Timestamp(datetime.combine(pd.Timestamp(day).date(),time(h,m),tzinfo=NY)).tz_convert('UTC')
def context():
 p=load_panels()['qqq'];sig=weekly_indices(p.dates);mask=eligible(p)&(moving_average(p,50)>moving_average(p,200))&liquid_mask(p,.5);return p,{s:select_equal(trailing_return(p,126,s),mask,sig) for s in SKIPS}
def ledger():
 p,weights=context();OUT.mkdir(parents=True,exist_ok=True);rows=[]
 for s,w in weights.items():
  exe=np.zeros_like(w);exe[1:]=w[:-1];exe=np.where(np.isfinite(p.adj_open),exe,0);prev=np.zeros(p.n_symbols)
  for i,day in enumerate(p.dates):
   delta=exe[i]-prev
   for c in np.flatnonzero(np.abs(delta)>1e-12):
    side='buy' if delta[c]>0 else 'sell';role='entry_ask_after' if side=='buy' else 'exit_bid_after'
    for label,clock in [('0930','09:30'),('0940','09:40')]:rows.append({'skip':s,'session_date':pd.Timestamp(day).normalize(),'symbol':str(p.symbols[c]),'side':side,'delta_weight':float(abs(delta[c])),'target_ts':utc(day,clock),'clock':label,'role':role})
   prev=exe[i].copy()
 x=pd.DataFrame(rows);x.to_parquet(OUT/'quote_ledger.parquet',index=False)
 for label,g in x.groupby('clock'):
  roles=g[['symbol','target_ts','role']].drop_duplicates();q=shared.quote_cache(label);m=roles.merge(q[['symbol','target_ts','role']],on=['symbol','target_ts','role'],how='left',indicator=True);miss=m[m._merge.eq('left_only')][['symbol','target_ts','role']];roles.to_parquet(OUT/f'roles_{label}.parquet',index=False);miss.to_parquet(OUT/f'missing_{label}.parquet',index=False);print(label,'roles',len(roles),'missing',len(miss))
def replay():
 p,weights=context();x=pd.read_parquet(OUT/'quote_ledger.parquet');x.target_ts=pd.to_datetime(x.target_ts,utc=True);parts=[]
 for label,g in x.groupby('clock'):
  q=shared.quote_cache(label);parts.append(g.merge(q[['symbol','target_ts','role','quote_ts','bid_price','ask_price']],on=['symbol','target_ts','role'],how='left',validate='many_to_one'))
 f=pd.concat(parts,ignore_index=True)
 rows=[];split=p.dates[int(len(p.dates)*.6)]
 for s,w in weights.items():
  g=f[f['skip'].eq(s)];ref=g[g.clock.eq('0930')].copy();ref['mid']=(ref.bid_price+ref.ask_price)/2;ref=ref[['session_date','symbol','side','mid']];fill=g[g.clock.eq('0940')].merge(ref,on=['session_date','symbol','side'],validate='one_to_one')
  x=fill.symbol.eq('XLNX')&fill.session_date.eq(pd.Timestamp('2022-02-14'))&fill.side.eq('sell')
  if x.any():
   base=ROOT/'campaigns'/'CAM-0600'/'artifacts'/'RUN-0042';a=pd.read_parquet(base/'xlnx_reference_quote.parquet').iloc[0];b=pd.read_parquet(base/'xlnx_terminal_quote.parquet').iloc[0];fill.loc[x,'mid']=(float(a.bid_price)+float(a.ask_price))/2;fill.loc[x,'bid_price']=float(b.bid_price);fill.loc[x,'ask_price']=float(b.ask_price);fill.loc[x,'session_date']=pd.Timestamp('2022-02-11')
  x=fill.symbol.eq('ALXN')&fill.session_date.eq(pd.Timestamp('2021-07-21'))&fill.side.eq('sell')
  if x.any():
   base=ROOT/'campaigns'/'CAM-0600'/'artifacts'/'RUN-0044';refs=pd.read_parquet(base/'terminal_reference_quotes.parquet');terms=pd.read_parquet(base/'terminal_exception_quotes.parquet');a=refs[refs.symbol.eq('ALXN')].iloc[0];b=terms[terms.symbol.eq('ALXN')].iloc[0];fill.loc[x,'mid']=(float(a.bid_price)+float(a.ask_price))/2;fill.loc[x,'bid_price']=float(b.bid_price);fill.loc[x,'ask_price']=float(b.ask_price);fill.loc[x,'session_date']=pd.Timestamp('2021-07-20')
  ok=fill.bid_price.notna()&fill.ask_price.notna()&fill.mid.notna()&(fill.bid_price>0)&(fill.ask_price>=fill.bid_price)&(fill.mid>0)
  if not ok.all():raise RuntimeError(f'incomplete roles skip={s} n={int((~ok).sum())}')
  _,d,*_=evaluate_weights(p,w,0.,holding='open_to_next_open',execution_lag=1)
  for bps in [0.,1.,2.,5.,10.]:
   cost=np.where(fill.side.eq('buy'),fill.delta_weight*(fill.ask_price/fill.mid-1),fill.delta_weight*(1-fill.bid_price/fill.mid))+fill.delta_weight.to_numpy(float)*bps/10000;cd=pd.Series(cost,index=pd.to_datetime(fill.session_date)).groupby(level=0).sum();net=d.gross_pnl.subtract(cd,fill_value=0);eq=1+net.cumsum();dd=(eq.cummax()-eq)/eq.cummax();mo=net.groupby(net.index.to_period('M')).sum();recent=net[net.index>=pd.Timestamp('2025-05-01')];rows.append({'skip':s,'extra_bps':bps,'return':net.sum(),'maximum_drawdown':dd.max(),'positive_months':(mo>0).sum(),'negative_months':(mo<0).sum(),'worst_month':mo.min(),'recent12_return':recent.sum(),'turnover':fill.delta_weight.sum(),**period_metrics(net,split)})
   if bps==2:pd.DataFrame({'date':net.index,'net_pnl':net.values}).to_parquet(OUT/f'quote_daily_skip{s}_2bps.parquet',index=False)
 out=pd.DataFrame(rows);out.to_csv(OUT/'quote_metrics.csv',index=False);report={'status':'completed','role_coverage':1.0,'maximum_loaded_date':'2026-04-30','holdout_rows_loaded':0,'metrics':rows};(OUT/'quote_report.json').write_text(json.dumps(report,indent=2,default=lambda v:v.item() if hasattr(v,'item') else v)+'\n');print(out[out.extra_bps.eq(2)].sort_values('skip').to_string(index=False))
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('phase',choices=['ledger','replay']);z=a.parse_args();{'ledger':ledger,'replay':replay}[z.phase]()
