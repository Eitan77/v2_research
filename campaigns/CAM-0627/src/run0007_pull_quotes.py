from __future__ import annotations
import gzip,json,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import pandas as pd,requests
ROOT=Path(__file__).resolve().parents[3];R3=ROOT/'campaigns'/'CAM-0627'/'artifacts'/'RUN-0003';OUT=ROOT/'campaigns'/'CAM-0627'/'artifacts'/'RUN-0007';RAW=OUT/'raw_quotes';SYMBOLS='VOO,QQQ,QQQM'
def env():
 e={}
 for z in (ROOT/'.env.local').read_text().splitlines():
  if '=' in z and not z.strip().startswith('#'):k,v=z.split('=',1);e[k.strip()]=v.strip().strip("\"'")
 return e
def fetch(j,e):
 path=RAW/f"{j['date']}_{j['label']}.json.gz";st=pd.Timestamp(j['date']+' '+j['start'],tz='America/New_York').tz_convert('UTC');en=pd.Timestamp(j['date']+' '+j['end'],tz='America/New_York').tz_convert('UTC');u=e.get('ALPACA_DATA_BASE_URL','https://data.alpaca.markets').rstrip('/')+'/v2/stocks/quotes';h={'APCA-API-KEY-ID':e['ALPACA_API_KEY_ID'],'APCA-API-SECRET-KEY':e['ALPACA_API_SECRET_KEY']};params={'symbols':SYMBOLS,'start':st.isoformat(),'end':en.isoformat(),'feed':'sip','limit':10000,'sort':'asc'};q={s:[] for s in SYMBOLS.split(',')};pages=0;tok=None
 while True:
  p=dict(params)
  if tok:p['page_token']=tok
  for retry in range(10):
   r=requests.get(u,headers=h,params=p,timeout=90)
   if r.status_code in (429,500,502,503,504):time.sleep(min(30,1.5*(retry+1)));continue
   r.raise_for_status();break
  else:raise RuntimeError('quote request exhausted retries')
  z=r.json();pages+=1
  for s,v in (z.get('quotes') or {}).items():q[s].extend(v)
  tok=z.get('next_page_token')
  if not tok:break
 RAW.mkdir(parents=True,exist_ok=True)
 with gzip.open(path,'wb') as h:h.write(json.dumps({'request':j,'pages':pages,'quotes':q},separators=(',',':')).encode())
 return {**j,'rows':sum(len(v) for v in q.values()),'pages':pages}
def main():
 base=pd.read_parquet(R3/'manifest.parquet');js=base[['date','label','start','end']].to_dict('records');e=env();rows=[]
 with ThreadPoolExecutor(max_workers=4) as p:
  fs={p.submit(fetch,j,e):j for j in js}
  for i,f in enumerate(as_completed(fs),1):
   try:rows.append(f.result())
   except Exception as x:rows.append({**fs[f],'error':str(x)})
   if i%6==0:print(f'completed={i}/{len(js)}',flush=True)
 m=pd.DataFrame(rows);OUT.mkdir(parents=True,exist_ok=True);m.to_parquet(OUT/'manifest.parquet',index=False);report={'status':'completed' if 'error' not in m or m.error.isna().all() else 'incomplete_blocked','planned_windows':72,'executed_windows':len(m),'failed_windows':int(m.error.notna().sum()) if 'error' in m else 0,'quote_rows':int(m.rows.fillna(0).sum()),'pages':int(m.pages.fillna(0).sum()),'maximum_date':str(pd.to_datetime(m.date).max().date()),'holdout_rows_loaded':0};(OUT/'retrieval_report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
