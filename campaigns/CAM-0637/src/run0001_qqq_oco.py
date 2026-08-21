from __future__ import annotations
import json, math, os, time
from pathlib import Path
import duckdb, numpy as np, pandas as pd, requests

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/'campaigns'/'CAM-0637'; OUT=CAM/'artifacts'/'RUN-0001'; RAW=OUT/'quotes'; CAT=Path(r'D:\AlgoResearch\data\catalog.duckdb')

def signals():
 c=duckdb.connect(str(CAT),read_only=True); x=c.execute("""select date,try_cast(timestamp as timestamptz) ts,arg_max(open,try_cast(ingested_at as timestamp)) as "open",arg_max(close,try_cast(ingested_at as timestamp)) as "close",arg_max(volume,try_cast(ingested_at as timestamp)) as volume from bars_1m where date between date '2025-04-01' and date '2025-05-31' and feed='sip' and adjustment='raw' and symbol='QQQ' and strftime(try_cast(timestamp as timestamptz) at time zone 'America/New_York','%H:%M') between '09:30' and '15:45' group by 1,2 order by 1,2""").fetchdf(); c.close()
 x['date']=pd.to_datetime(x.date); x['ts']=pd.to_datetime(x.ts,utc=True); x['hhmm']=x.ts.dt.tz_convert('America/New_York').dt.strftime('%H:%M'); x['green_bp']=(x.close/x.open-1)*1e4
 x['med']=x.groupby('hhmm').volume.transform(lambda s:s.shift(1).rolling(20,min_periods=10).median()); x['rvol']=x.volume/x.med
 y=x[(x.date>=pd.Timestamp('2025-05-01'))&(x.green_bp>=5)&(x.rvol>=2)].copy(); y['entry_target']=y.ts+pd.Timedelta(minutes=1); return y

def creds():
 v=dict(os.environ); p=ROOT/'.env.local'
 for line in p.read_text().splitlines():
  if line.strip() and not line.lstrip().startswith('#') and '=' in line:
   k,z=line.split('=',1); v.setdefault(k.strip(),z.strip().strip('"').strip("'"))
 return v['ALPACA_API_KEY_ID'],v['ALPACA_API_SECRET_KEY']

def windows(s):
 w=sorted([[t,t+pd.Timedelta(minutes=5,seconds=2)] for t in s.entry_target]); out=[]
 for a,b in w:
  if out and a<=out[-1][1]:out[-1][1]=max(out[-1][1],b)
  else:out.append([a,b])
 return out

def quotes(s):
 RAW.mkdir(parents=True,exist_ok=True); key,sec=creds(); frames=[]; cov=[]; sess=requests.Session(); url='https://data.alpaca.markets/v2/stocks/QQQ/quotes'
 for i,(a,b) in enumerate(windows(s)):
  p=RAW/f'{i:04d}.parquet'
  if p.exists():x=pd.read_parquet(p)
  else:
   rows=[]; tok=None
   while True:
    pa={'start':a.isoformat(),'end':b.isoformat(),'feed':'sip','limit':10000,'sort':'asc'}
    if tok:pa['page_token']=tok
    for n in range(7):
     r=sess.get(url,params=pa,headers={'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':sec},timeout=60)
     if r.status_code==429:time.sleep(min(20,2**n));continue
     r.raise_for_status();break
    body=r.json();rows+=body.get('quotes',[]);tok=body.get('next_page_token')
    if not tok:break
   x=pd.DataFrame({'quote_ts':[r.get('t') for r in rows],'bid':[r.get('bp') for r in rows],'ask':[r.get('ap') for r in rows]}); x['quote_ts']=pd.to_datetime(x.quote_ts,utc=True);x.to_parquet(p,index=False)
  frames.append(x);cov.append(len(x))
 sess.close(); q=pd.concat(frames,ignore_index=True).drop_duplicates().sort_values('quote_ts');return q,cov

def replay(s,q,target,hold,slip):
 rows=[];last=np.datetime64('1970-01-01')
 qdays=q if isinstance(q,dict) else {d:z.reset_index(drop=True) for d,z in q.groupby(q.quote_ts.dt.date)}
 for d,sd in s.groupby('date'):
  z=qdays.get(d.date())
  if z is None:continue
  ts=z.quote_ts.to_numpy(dtype='datetime64[ns]');bid=z.bid.to_numpy();ask=z.ask.to_numpy()
  for e in sd.itertuples():
   t=np.datetime64(e.entry_target.to_datetime64())
   if t<=last:continue
   i=int(np.searchsorted(ts,t));
   if i>=len(z) or ts[i]>t+np.timedelta64(2,'s'):continue
   entry=ask[i];limit=math.ceil(entry*(1+target/1e4)*100-1e-10)/100;dist=limit-entry;stop=entry-dist;end=min(int(np.searchsorted(ts,t+np.timedelta64(hold,'m'))),len(z)-1);out='timeout';j=end
   for k in range(i+1,end+1):
    if bid[k]>=limit:out='target';j=k;break
    if bid[k]<=stop:out='stop';j=k;break
   ex=limit if out=='target' else bid[j]*(1-slip/1e4);rows.append({'date':d,'entry':entry,'limit':limit,'stop':stop,'outcome':out,'return':ex/entry-1,'distance_bp':dist/entry*1e4,'spread_bp':(ask[i]/bid[i]-1)*1e4});last=ts[j]
 return pd.DataFrame(rows)

def main():
 OUT.mkdir(parents=True,exist_ok=True);s=signals();q,cov=quotes(s);res={};led=[]
 qdays={d:z.reset_index(drop=True) for d,z in q.groupby(q.quote_ts.dt.date)}
 for t in [1,2]:
  for h in [1,3,5]:
   for slip in [0,1]:
    x=replay(s,qdays,t,h,slip);k=f't{t}_h{h}_s{slip}';m={'trades':len(x),'net_return':float(x['return'].sum()),'mean_bp':float(x['return'].mean()*1e4),'target_rate':float((x.outcome=='target').mean()),'stop_rate':float((x.outcome=='stop').mean()),'timeout_rate':float((x.outcome=='timeout').mean()),'mean_distance_bp':float(x.distance_bp.mean()),'mean_spread_bp':float(x.spread_bp.mean())};res[k]=m;x.assign(config=k).to_csv(OUT/f'ledger_{k}.csv',index=False)
 report={'signals':len(s),'windows':len(cov),'zero_windows':sum(n==0 for n in cov),'quote_rows':len(q),'results':res};(OUT/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
