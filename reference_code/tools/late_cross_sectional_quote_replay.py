from __future__ import annotations
import argparse,json,time,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import duckdb,numpy as np,pandas as pd
sys.path.insert(0,r'D:\AlgoResearch\src')
from ar_pipeline.marketdata import AlpacaHistoricalClient

LAT=250; WAIT=2; SYMS=('TQQQ','SQQQ','SOXL','SOXS')
def iso(ts): return pd.Timestamp(ts).tz_convert('UTC').isoformat().replace('+00:00','Z')
def build(cat):
 marks=','.join('?' for _ in SYMS)
 sql=f'''with b as (select symbol,session_date,cast(timestamp as timestamptz) at time zone 'America/New_York' et,open,close from research_matrix where timeframe='5m' and session_date between '2025-12-30' and '2026-04-30' and symbol in ({marks})),x as (select *,extract(hour from et)*60+extract(minute from et) m from b),op as (select symbol,session_date,max(open) filter(where m=570) session_open from x group by 1,2),s as (select x.symbol,x.session_date,x.close/op.session_open-1 move_ret from x join op using(symbol,session_date) where x.m=900 and op.session_open>0),r as (select *,row_number() over(partition by session_date order by move_ret desc,symbol) rn from s),e as (select r.symbol,r.session_date,r.move_ret,e.open entry_open from r join x e using(symbol,session_date) where r.rn=1 and r.move_ret>=0.02 and e.m=905),q as (select *,make_timestamp(cast(strftime(cast(session_date as date),'%Y') as int),cast(strftime(cast(session_date as date),'%m') as int),cast(strftime(cast(session_date as date),'%d') as int),15,5,0) entry_et,make_timestamp(cast(strftime(cast(session_date as date),'%Y') as int),cast(strftime(cast(session_date as date),'%m') as int),cast(strftime(cast(session_date as date),'%d') as int),15,25,0) exit_et from e where session_date between '2026-01-01' and '2026-04-30') select * from q order by session_date'''
 c=duckdb.connect(cat,read_only=True);c.execute('set threads=16')
 try:d=c.execute(sql,list(SYMS)).fetchdf()
 finally:c.close()
 d['entry_request_ts']=pd.to_datetime(d.entry_et).dt.tz_localize('America/New_York')+pd.Timedelta(milliseconds=LAT);d['exit_request_ts']=pd.to_datetime(d.exit_et).dt.tz_localize('America/New_York')+pd.Timedelta(milliseconds=LAT);return d[['session_date','symbol','move_ret','entry_request_ts','exit_request_ts']]
def fetch(client,r,cache):
 p=cache/r.symbol;p.mkdir(parents=True,exist_ok=True);f=p/(pd.Timestamp(r.entry_request_ts).strftime('%Y%m%dT%H%M%S')+'.json');start=r.entry_request_ts;end=r.exit_request_ts+pd.Timedelta(seconds=WAIT)
 if f.exists():
  try:return json.loads(f.read_text())['rows']
  except:pass
 pages,_=client._paged_json('/v2/stocks/quotes',{'symbols':r.symbol,'start':iso(start),'end':iso(end),'feed':'sip','limit':10000,'sort':'asc'});rows=[]
 for page in pages:
  for _,vals in page.get('quotes',{}).items():
   rows += [{'t':v.get('t'),'bp':v.get('bp'),'ap':v.get('ap')} for v in vals]
 f.write_text(json.dumps({'request':{'symbol':r.symbol,'start':iso(start),'end':iso(end)},'rows':rows},),encoding='utf-8');return rows
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--catalog',default='D:/AlgoResearch/data/catalog.duckdb');ap.add_argument('--out',required=True);ap.add_argument('--workers',type=int,default=16);a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True);d=build(a.catalog);d.to_parquet(out/'holdout_source_trades.parquet',index=False);client=AlpacaHistoricalClient.from_env(r'D:\AlgoResearch\.env');client.max_retries=20;client.requests_per_minute=200;cache=out/'quote_cache';rows=[];t=time.perf_counter()
 with ThreadPoolExecutor(max_workers=a.workers) as pool:
  fut={pool.submit(fetch,client,r,cache):i for i,r in enumerate(d.itertuples(index=False))}
  for f in as_completed(fut):
   r=d.iloc[fut[f]]
   try:raw=f.result()
   except Exception as e:rows.append({'session_date':r.session_date,'symbol':r.symbol,'status':'fetch_error','reason':str(e)});continue
   q=pd.DataFrame(raw)
   if q.empty:rows.append({'session_date':r.session_date,'symbol':r.symbol,'status':'missing_path'});continue
   q['ts']=pd.to_datetime(q.t,utc=True,errors='coerce');q['bid']=pd.to_numeric(q.bp,errors='coerce');q['ask']=pd.to_numeric(q.ap,errors='coerce');q=q[(q.ts.notna())&(q.bid>0)&(q.ask>q.bid)].sort_values('ts');eq=q[q.ts>=r.entry_request_ts.tz_convert('UTC')]
   if eq.empty:rows.append({'session_date':r.session_date,'symbol':r.symbol,'status':'missing_entry'});continue
   e=eq.iloc[0];entry=float(e.ask);stop=entry*(1-.0005);take=entry*(1+.02);path=q[(q.ts>=e.ts)&(q.ts<=r.exit_request_ts.tz_convert('UTC')+pd.Timedelta(seconds=WAIT))];sq=path[path.bid<=stop];tq=path[path.bid>=take]
   if len(sq) and len(tq):ex=sq.iloc[0] if sq.iloc[0].ts<=tq.iloc[0].ts else tq.iloc[0];reason='stop' if ex.ts==sq.iloc[0].ts else 'take'
   elif len(sq):ex=sq.iloc[0];reason='stop'
   elif len(tq):ex=tq.iloc[0];reason='take'
   else:
    xx=path[path.ts>=r.exit_request_ts.tz_convert('UTC')]
    if xx.empty:rows.append({'session_date':r.session_date,'symbol':r.symbol,'status':'missing_exit','entry_price':entry});continue
    ex=xx.iloc[0];reason='time'
   rows.append({'session_date':r.session_date,'symbol':r.symbol,'move_ret':r.move_ret,'status':'filled','entry_price':entry,'exit_price':float(ex.bid),'gross_return':float(ex.bid)/entry-1,'exit_reason':reason,'entry_spread_bps':float((e.ask-e.bid)/((e.ask+e.bid)/2)*10000),'quote_count':len(q)})
 led=pd.DataFrame(rows);led.to_parquet(out/'holdout_quote_ledger.parquet',index=False);filled=led[led.status.eq('filled')].copy();summ=[]
 for c in (0,1,2,5,10):
  z=filled.gross_return.to_numpy()-2*c/10000 if len(filled) else np.array([]);eq=np.cumsum(z);summ.append(dict(cost_bps_side=c,trades=len(z),fill_rate=float(len(z)/len(led)) if len(led) else 0,simple_pnl=float(z.sum()) if len(z) else np.nan,mean_return=float(z.mean()) if len(z) else np.nan,win_rate=float((z>0).mean()) if len(z) else np.nan,max_drawdown=float(np.min(eq-np.maximum.accumulate(eq))) if len(z) else np.nan))
 pd.DataFrame(summ).to_csv(out/'holdout_quote_summary.csv',index=False);(out/'run_metadata.json').write_text(json.dumps({'holdout':['2026-01-01','2026-04-30'],'signal':'15:00 strongest leveraged ETF move >=200bp; entry 15:05; TP200/SL5; exit 15:25','latency_ms':LAT,'feed':'sip','workers':a.workers,'elapsed_sec':time.perf_counter()-t},indent=2),encoding='utf-8');print(pd.DataFrame(summ).to_string(index=False))
if __name__=='__main__':main()
