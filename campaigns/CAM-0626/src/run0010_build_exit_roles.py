from __future__ import annotations
import gzip,json,multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];A=ROOT/'campaigns'/'CAM-0626'/'artifacts';OUT=A/'RUN-0010';RAW=OUT/'raw_api'
def ts(x):return pd.to_datetime(x,utc=True)
def fill_time(quotes,trades,order_ts,target):
 qs=sorted([(ts(q['t']),float(q.get('ap') or 0),float(q.get('as') or 0)) for q in quotes if q.get('ap') and q.get('as')],key=lambda x:x[0]);tr=sorted([(ts(t['t']),float(t.get('p') or 0),float(t.get('s') or 0)) for t in trades if t.get('p') and t.get('s')],key=lambda x:x[0]);deadline=order_ts+pd.Timedelta(seconds=300);idx=next((i for i,q in enumerate(qs) if order_ts<=q[0]<=deadline and q[1]>0 and q[2]>0),None)
 if idx is None:return None,None
 limit=qs[idx][1];ahead=qs[idx][2];curp=limit;curs=qs[idx][2];lost=False;qi=idx+1;ti=0
 while ti<len(tr) and tr[ti][0]<qs[idx][0]:ti+=1
 while qi<len(qs) or ti<len(tr):
  q=qs[qi] if qi<len(qs) else None;t=tr[ti] if ti<len(tr) else None
  if q is not None and q[0]<=deadline and (t is None or q[0]<=t[0]):
   qi+=1;newp,newsz=q[1],q[2]
   if abs(newp-limit)>1e-8:lost=True;curp,curs=newp,newsz;continue
   if lost:ahead=max(ahead,newsz);lost=False
   curp,curs=newp,newsz;continue
  if t is None or t[0]>deadline:break
  ti+=1
  if lost or abs(curp-limit)>1e-8 or abs(t[1]-limit)>1e-8:continue
  used=min(ahead,t[2]);ahead-=used;available=max(0,t[2]-used)
  if available>=target:return t[0],limit
 return None,limit
def reconstruct(x):
 ds=pd.Timestamp(x['date']).strftime('%Y-%m-%d');raw={}
 for kind in ('quotes','trades'):
  with gzip.open(x[kind+'_path'],'rb') as h:raw[kind]=json.loads(h.read().decode()).get('rows',[])
 order=pd.Timestamp(ds+' 09:35',tz='America/New_York').tz_convert('UTC');ft,limit=fill_time(raw['quotes'],raw['trades'],order,x['target_qty']);return {**x,'entry_fill_ts':ft,'entry_fill':limit}
def main():
 f=pd.read_parquet(OUT/'passive_fills.parquet');f.date=pd.to_datetime(f.date);f=f[f.status.eq('filled')].copy();manifest=pd.read_parquet(OUT/'retrieval_manifest.parquet');paths={(x.session_date,x.symbol,x.kind):x.path for x in manifest.dropna(subset=['path']).itertuples()};jobs=[]
 for x in f.itertuples():
  ds=x.date.strftime('%Y-%m-%d');jobs.append({'date':x.date,'symbol':x.symbol,'target_qty':x.target_qty,'quotes_path':paths[(ds,x.symbol,'quotes')],'trades_path':paths[(ds,x.symbol,'trades')]})
 with ProcessPoolExecutor(max_workers=16) as pool:filled=list(pool.map(reconstruct,jobs,chunksize=1))
 filled=[x for x in filled if x['entry_fill_ts'] is not None];bars=pd.read_parquet(A/'RUN-0003'/'selected_bars_1m.parquet',columns=['date','symbol','ts','high']);bars.date=pd.to_datetime(bars.date);keys=pd.DataFrame(filled)[['date','symbol']].drop_duplicates();bars=bars.merge(keys,on=['date','symbol'],how='inner');bars.ts=pd.to_datetime(bars.ts,utc=True);groups={(d,s):g for (d,s),g in bars.groupby(['date','symbol'],sort=False)};rows=[]
 for x in filled:
  ds=pd.Timestamp(x['date']).strftime('%Y-%m-%d');ft=pd.Timestamp(x['entry_fill_ts']);limit=float(x['entry_fill']);symbol=x['symbol'];date=pd.Timestamp(x['date'])
  time_exit=pd.Timestamp(ds+' 10:35',tz='America/New_York').tz_convert('UTC');stop=limit*1.02;b=groups.get((date,symbol),pd.DataFrame());z=b[(b.ts>=ft.floor('min'))&(b.ts<time_exit)&b.high.ge(stop)].sort_values('ts') if len(b) else b
  target=(pd.Timestamp(z.iloc[0].ts)+pd.Timedelta(minutes=1)) if len(z) else time_exit;rows.append({'date':date,'symbol':symbol,'entry_fill_ts':ft,'entry_fill':limit,'target_ts':target,'role':'passive_short_exit_ask_after','exit_reason':'stop' if len(z) else 'time_exit','weight':1.0})
 l=pd.DataFrame(rows);l.to_parquet(OUT/'exit_ledger.parquet',index=False);l[['symbol','target_ts','role']].drop_duplicates().to_parquet(OUT/'roles_exit.parquet',index=False);print({'reported_filled_orders':len(f),'reconstructed_fills':len(l),'stops':int((l.exit_reason=='stop').sum()),'max_date':str(l.date.max().date()),'holdout_rows':0})
if __name__=='__main__':multiprocessing.freeze_support();main()
