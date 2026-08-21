from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor,as_completed
import json,os,threading,time
from pathlib import Path
import numpy as np,pandas as pd,requests

ROOT=Path(__file__).resolve().parents[3];CAM=ROOT/'campaigns'/'CAM-0639';OUT=CAM/'artifacts'/'RUN-0002';CACHE=ROOT/'tmp'/'cam0639_mu_endpoint_quotes.jsonl';URL='https://data.alpaca.markets/v2/stocks/MU/quotes';LOCK=threading.Lock();NEXT=[0.0]

def creds():
 v=dict(os.environ);p=ROOT/'.env.local'
 for line in p.read_text().splitlines():
  if line.strip() and not line.lstrip().startswith('#') and '=' in line:
   k,z=line.split('=',1);v.setdefault(k.strip(),z.strip().strip('"').strip("'"))
 return v['ALPACA_API_KEY_ID'],v['ALPACA_API_SECRET_KEY']

def throttle():
 with LOCK:
  now=time.monotonic();wait=max(0,NEXT[0]-now);NEXT[0]=max(now,NEXT[0])+.32
 if wait:time.sleep(wait)

def fetch(task,key,sec):
 role,target=task['role'],pd.Timestamp(task['target']);end=target+pd.Timedelta(seconds=10);headers={'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':sec};params={'start':target.isoformat().replace('+00:00','Z'),'end':end.isoformat().replace('+00:00','Z'),'feed':'sip','limit':10000,'sort':'asc'}
 for n in range(10):
  throttle();r=requests.get(URL,params=params,headers=headers,timeout=45)
  if r.status_code==429:time.sleep(min(30,2**n));continue
  r.raise_for_status();quotes=[q for q in r.json().get('quotes',[]) if q.get('bp',0)>0 and q.get('ap',0)>=q.get('bp',0)];q=quotes[0] if quotes else None
  return {'key':task['key'],'role':role,'target':target.isoformat(),'quote':q}
 raise RuntimeError(f"rate limit {task['key']}")

def load_cache():
 out={}
 if CACHE.exists():
  for line in CACHE.read_text().splitlines():
   try:z=json.loads(line);out[z['key']]=z
   except Exception:pass
 return out

def metrics(x,cost):
 y=x.copy();y['ret']=y.quote_return-2*cost/1e4;daily=y.groupby('exit_date').ret.sum();monthly=daily.resample('ME').sum();yearly=daily.resample('YE').sum();eq=1+daily.cumsum();dd=(eq.cummax()-eq)/eq.cummax()
 return {'net_return':float(y.ret.sum()),'mean_overnight_bp':float(y.ret.mean()*1e4),'win_rate':float((y.ret>0).mean()),'max_drawdown':float(dd.max()),'positive_months':int((monthly>0).sum()),'negative_months':int((monthly<0).sum()),'positive_years':int((yearly>0).sum()),'monthly':{str(k.date()):float(v) for k,v in monthly.items()},'yearly':{str(k.year):float(v) for k,v in yearly.items()}}

def main():
 OUT.mkdir(parents=True,exist_ok=True);roles=pd.read_parquet(CAM/'artifacts'/'RUN-0001'/'roles.parquet');tasks=[]
 for i,r in roles.iterrows():
  for role,col in [('entry','entry_target'),('exit','exit_target')]:tasks.append({'key':f'{i}|{role}','role':role,'target':r[col]})
 cache=load_cache();missing=[t for t in tasks if t['key'] not in cache];key,sec=creds();CACHE.parent.mkdir(parents=True,exist_ok=True)
 if missing:
  with CACHE.open('a',encoding='utf-8') as f,ThreadPoolExecutor(max_workers=6) as ex:
   futures=[ex.submit(fetch,t,key,sec) for t in missing]
   for n,fu in enumerate(as_completed(futures),1):
    z=fu.result();cache[z['key']]=z;f.write(json.dumps(z,separators=(',',':'))+'\n');f.flush()
    if n%100==0:print(f'quotes {len(cache)}/{len(tasks)}',flush=True)
 rows=[]
 for i,r in roles.iterrows():
  e=cache.get(f'{i}|entry',{}).get('quote');x=cache.get(f'{i}|exit',{}).get('quote');rows.append({**r.to_dict(),'entry_complete':e is not None,'exit_complete':x is not None,'entry_quote_ts':e.get('t') if e else None,'entry_bid':e.get('bp') if e else np.nan,'entry_ask':e.get('ap') if e else np.nan,'entry_ask_size':e.get('as') if e else np.nan,'exit_quote_ts':x.get('t') if x else None,'exit_bid':x.get('bp') if x else np.nan,'exit_ask':x.get('ap') if x else np.nan,'exit_bid_size':x.get('bs') if x else np.nan})
 ledger=pd.DataFrame(rows);ledger['quote_return']=ledger.exit_bid/ledger.entry_ask-1;ledger['bar_return']=ledger.exit_bar_open/ledger.entry_bar_close-1;done=ledger[ledger.entry_complete&ledger.exit_complete].copy();done['quote_drag']=done.quote_return-done.bar_return;done.to_csv(OUT/'trade_ledger.csv',index=False)
 result={'expected_overnights':len(roles),'complete_overnights':len(done),'missing_overnights':len(roles)-len(done),'maximum_exit_date':str(pd.to_datetime(roles.exit_date).max().date()),'bar_proxy_return':float(done.bar_return.sum()),'quote_return':float(done.quote_return.sum()),'quote_minus_bar_drag':float(done.quote_drag.sum()),'median_entry_spread_bp':float(((done.entry_ask/done.entry_bid-1)*1e4).median()),'median_exit_spread_bp':float(((done.exit_ask/done.exit_bid-1)*1e4).median()),'costs':{str(c):metrics(done,c) for c in [0,1,2,5]}}
 (OUT/'report.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='costs'}|{'cost_summary':{k:{z:m[z] for z in ['net_return','mean_overnight_bp','win_rate','max_drawdown']} for k,m in result['costs'].items()}},indent=2))
if __name__=='__main__':main()
