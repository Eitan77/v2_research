from __future__ import annotations
import json
from pathlib import Path
import requests,pandas as pd
ROOT=Path(__file__).resolve().parents[3];SYMS=['ARM','MU','SNDK']
def env():
 out={}
 for line in (ROOT/'.env.local').read_text().splitlines():
  if '=' in line and not line.lstrip().startswith('#'):
   k,v=line.split('=',1);out[k.strip()]=v.strip().strip("\"'")
 return out
def main():
 e=env();h={'APCA-API-KEY-ID':e['ALPACA_API_KEY_ID'],'APCA-API-SECRET-KEY':e['ALPACA_API_SECRET_KEY']};base=e.get('ALPACA_DATA_BASE_URL','https://data.alpaca.markets').rstrip('/')
 end=pd.Timestamp.now(tz='UTC')-pd.Timedelta(minutes=16);start=end-pd.Timedelta(minutes=30);rows=[]
 for s in SYMS:
  r=requests.get(base+f'/v2/stocks/{s}/quotes',headers=h,params={'start':'2026-08-10T13:40:00Z','end':'2026-08-10T13:40:05Z','feed':'sip','limit':1000,'sort':'asc'},timeout=30);r.raise_for_status();q=r.json()['quotes'][0];entry=269.46 if s=='ARM' else (q['bp']+q['ap'])/2
  rr=requests.get(base+f'/v2/stocks/{s}/quotes',headers=h,params={'start':start.isoformat(),'end':end.isoformat(),'feed':'sip','limit':10000,'sort':'desc'},timeout=30);rr.raise_for_status();quotes=rr.json()['quotes'];
  if not quotes:raise RuntimeError(f'no delayed quote for {s}')
  now=quotes[0];mid=(now['bp']+now['ap'])/2;rows.append({'symbol':s,'entry_mark':entry,'latest_bid':now['bp'],'latest_ask':now['ap'],'latest_mid':mid,'quote_ts':now['t'],'mid_return_pct':100*(mid/entry-1),'bid_liquidation_return_pct':100*(now['bp']/entry-1)})
 out={'generated_utc':pd.Timestamp.now(tz='UTC').isoformat(),'positions':rows,'equal_weight_mid_return_pct':sum(x['mid_return_pct'] for x in rows)/3,'equal_weight_bid_liquidation_return_pct':sum(x['bid_liquidation_return_pct'] for x in rows)/3};print(json.dumps(out,indent=2));
if __name__=='__main__':main()
