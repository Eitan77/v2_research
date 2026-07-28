from __future__ import annotations
import argparse, json, os, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import duckdb, numpy as np, pandas as pd
import sys
sys.path.insert(0,r'D:\AlgoResearch\src')
from ar_pipeline.marketdata import AlpacaHistoricalClient

START='2026-01-01'; END='2026-04-30'; LAT_MS=250; WAIT_SEC=2; FEED='sip'
def iso(ts): return pd.Timestamp(ts).tz_convert('UTC').isoformat().replace('+00:00','Z')
def build(catalog):
 sql='''with b as (select symbol,session_date,cast(timestamp as timestamptz) at time zone 'America/New_York' et,open,close from research_matrix where timeframe='5m' and session_date between '2025-12-30' and '2026-04-30' and symbol in ('QQQ','TQQQ','SQQQ')), x as (select *,extract(hour from et)*60+extract(minute from et) m from b), d0 as (select symbol,session_date,max(open) filter(where m=570) session_open,max(close) filter(where m=955) session_close from x group by 1,2), d as (select *,lag(session_close) over(partition by symbol order by session_date) prev_close from d0), l as (select x.session_date,x.close/d.prev_close-1 leader_ret from x join d using(symbol,session_date) where x.symbol='QQQ' and x.m=900 and d.prev_close>0), e as (select l.session_date,l.leader_ret,case when l.leader_ret>=0 then 'TQQQ' else 'SQQQ' end symbol,make_timestamp(cast(strftime(cast(l.session_date as date),'%Y') as int),cast(strftime(cast(l.session_date as date),'%m') as int),cast(strftime(cast(l.session_date as date),'%d') as int),15,30,0) entry_et,make_timestamp(cast(strftime(cast(l.session_date as date),'%Y') as int),cast(strftime(cast(l.session_date as date),'%m') as int),cast(strftime(cast(l.session_date as date),'%d') as int),15,35,0) exit_et from l where l.session_date between '2026-01-01' and '2026-04-30') select * from e order by session_date'''
 c=duckdb.connect(catalog,read_only=True); c.execute('set threads=16');
 try: d=c.execute(sql).fetchdf()
 finally: c.close()
 d['entry_request_ts']=pd.to_datetime(d.entry_et).dt.tz_localize('America/New_York')+pd.Timedelta(milliseconds=LAT_MS); d['exit_request_ts']=pd.to_datetime(d.exit_et).dt.tz_localize('America/New_York')+pd.Timedelta(milliseconds=LAT_MS); return d[['session_date','symbol','leader_ret','entry_request_ts','exit_request_ts']]
def fetch_one(client,rec,cache):
 sym=rec.symbol; start=rec.entry_request_ts; end=rec.exit_request_ts+pd.Timedelta(seconds=WAIT_SEC); path=cache/sym; path.mkdir(parents=True,exist_ok=True); f=path/(pd.Timestamp(start).strftime('%Y%m%dT%H%M%S')+'.json')
 if f.exists():
  try:return f,json.loads(f.read_text())['rows']
  except Exception: pass
 pages,_=client._paged_json('/v2/stocks/quotes',{'symbols':sym,'start':iso(start),'end':iso(end),'feed':FEED,'limit':10000,'sort':'asc'}); rows=[]
 for page in pages:
  for _,vals in page.get('quotes',{}).items():
   for v in vals: rows.append({'S':sym,'t':v.get('t'),'bp':v.get('bp'),'ap':v.get('ap'),'bs':v.get('bs'),'as':v.get('as')})
 payload={'request':{'symbol':sym,'start':iso(start),'end':iso(end),'feed':FEED},'rows':rows,'row_count':len(rows)}; f.write_text(json.dumps(payload),encoding='utf-8'); return f,rows
def replay(d,cache,workers):
 client=AlpacaHistoricalClient.from_env(r'D:\AlgoResearch\.env'); client.max_retries=20; client.requests_per_minute=200; results=[]; t=time.perf_counter()
 with ThreadPoolExecutor(max_workers=workers) as pool:
  fut={pool.submit(fetch_one,client,r,cache):i for i,r in enumerate(d.itertuples(index=False))}
  for f in as_completed(fut):
   i=fut[f]; rec=d.iloc[i];
   try: _,rows=f.result()
   except Exception as e: results.append(dict(session_date=rec.session_date,symbol=rec.symbol,status='fetch_error',reason=str(e))); continue
   q=pd.DataFrame(rows)
   if q.empty: results.append(dict(session_date=rec.session_date,symbol=rec.symbol,status='missing_path')); continue
   q['quote_ts']=pd.to_datetime(q.t,utc=True,errors='coerce'); q['bid']=pd.to_numeric(q.bp,errors='coerce'); q['ask']=pd.to_numeric(q.ap,errors='coerce'); q=q[(q.quote_ts.notna())&(q.bid>0)&(q.ask>q.bid)].sort_values('quote_ts')
   entry=q[q.quote_ts>=rec.entry_request_ts.tz_convert('UTC')]
   if entry.empty: results.append(dict(session_date=rec.session_date,symbol=rec.symbol,status='missing_entry')); continue
   eq=entry.iloc[0]; entry_px=float(eq.ask); stop=entry_px*(1-.0005); take=entry_px*(1+.005); path=q[(q.quote_ts>=eq.quote_ts)&(q.quote_ts<=rec.exit_request_ts.tz_convert('UTC')+pd.Timedelta(seconds=WAIT_SEC))]
   stopq=path[path.bid<=stop]; takeq=path[path.bid>=take]
   if len(stopq) and len(takeq): ex=stopq.iloc[0] if stopq.iloc[0].quote_ts<=takeq.iloc[0].quote_ts else takeq.iloc[0]; reason='stop' if ex.quote_ts==stopq.iloc[0].quote_ts else 'take'
   elif len(stopq): ex=stopq.iloc[0]; reason='stop'
   elif len(takeq): ex=takeq.iloc[0]; reason='take'
   else:
    exq=path[path.quote_ts>=rec.exit_request_ts.tz_convert('UTC')]
    if exq.empty: results.append(dict(session_date=rec.session_date,symbol=rec.symbol,status='missing_exit',entry_price=entry_px)); continue
    ex=exq.iloc[0]; reason='time'
   spread=((float(eq.ask)-float(eq.bid))/((float(eq.ask)+float(eq.bid))/2))*10000; gross=float(ex.bid)/entry_px-1
   results.append(dict(session_date=rec.session_date,symbol=rec.symbol,leader_ret=rec.leader_ret,status='filled',entry_price=entry_px,exit_price=float(ex.bid),gross_return=gross,exit_reason=reason,entry_quote_ts=eq.quote_ts,exit_quote_ts=ex.quote_ts,entry_spread_bps=spread,quote_count=len(q)))
 print('replayed',len(results),'elapsed',round(time.perf_counter()-t,1),flush=True); return pd.DataFrame(results)
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--catalog',default='D:/AlgoResearch/data/catalog.duckdb'); ap.add_argument('--out',required=True); ap.add_argument('--workers',type=int,default=16); a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True); d=build(a.catalog); d.to_parquet(out/'holdout_source_trades.parquet',index=False); led=replay(d,out/'quote_cache',a.workers); led.to_parquet(out/'holdout_quote_ledger.parquet',index=False); filled=led[led.status.eq('filled')].copy(); rows=[]
 for cost in (0,1,2,5,10):
  if len(filled):
   z=filled.gross_return.to_numpy()-2*cost/10000; eq=np.cumsum(z); dd=float(np.min(eq-np.maximum.accumulate(eq))); rows.append(dict(cost_bps_side=cost,trades=len(z),fill_rate=len(z)/len(led),simple_pnl=float(z.sum()),mean_return=float(z.mean()),win_rate=float((z>0).mean()),max_drawdown=dd,positive_months=int((filled.assign(z=z).groupby(pd.to_datetime(filled.session_date).dt.to_period('M')).z.sum()>0).sum())))
 pd.DataFrame(rows).to_csv(out/'holdout_quote_summary.csv',index=False); (out/'run_metadata.json').write_text(json.dumps({'holdout':[START,END],'latency_ms':LAT_MS,'wait_sec':WAIT_SEC,'feed':FEED,'workers':a.workers,'source':'frozen QQQ prior-close direction; 15:30 TQQQ/SQQQ; TP50/SL5'},indent=2),encoding='utf-8'); print(pd.DataFrame(rows).to_string(index=False))
if __name__=='__main__': main()
