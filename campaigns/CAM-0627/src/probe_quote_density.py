import requests,pandas as pd
from pathlib import Path
env={}
for line in (Path(__file__).resolve().parents[3]/'.env.local').read_text().splitlines():
 if '=' in line and not line.strip().startswith('#'):
  k,v=line.split('=',1);env[k.strip()]=v.strip().strip("\"'")
u=env.get('ALPACA_DATA_BASE_URL','https://data.alpaca.markets').rstrip('/')+'/v2/stocks/quotes';h={'APCA-API-KEY-ID':env['ALPACA_API_KEY_ID'],'APCA-API-SECRET-KEY':env['ALPACA_API_SECRET_KEY']};p={'symbols':'SPY,IVV','start':'2026-03-02T14:35:00Z','end':'2026-03-02T14:36:00Z','feed':'sip','limit':10000,'sort':'asc'};n=pages=0
while True:
 r=requests.get(u,headers=h,params=p,timeout=60);r.raise_for_status();x=r.json();n+=sum(len(v) for v in (x.get('quotes') or {}).values());pages+=1;tok=x.get('next_page_token')
 if not tok:break
 p['page_token']=tok
print({'symbols':'SPY,IVV','minute':'2026-03-02 09:35 ET','quotes':n,'pages':pages})
