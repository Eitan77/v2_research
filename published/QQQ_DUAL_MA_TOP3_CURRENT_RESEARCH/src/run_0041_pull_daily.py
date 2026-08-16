from __future__ import annotations
import json,time
from pathlib import Path
import duckdb,pandas as pd,requests
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0041";END=pd.Timestamp("2026-08-01",tz="UTC")
def env():
 d={}
 for raw in (ROOT/".env.local").read_text().splitlines():
  if "=" in raw and not raw.strip().startswith("#"):k,v=raw.split("=",1);d[k.strip()]=v.strip().strip("\"'")
 return d
def main():
 OUT.mkdir(parents=True,exist_ok=True);c=duckdb.connect(r"D:\AlgoResearch\data\catalog.duckdb",read_only=True);m=c.execute("select distinct symbol from qqq_pit_membership_daily where try_cast(date as date) between date '2026-05-01' and date '2026-07-31' and is_member").fetchdf();c.close();symbols=sorted(m.symbol.astype(str).unique());e=env();s=requests.Session();s.headers.update({"APCA-API-KEY-ID":e["ALPACA_API_KEY_ID"],"APCA-API-SECRET-KEY":e["ALPACA_API_SECRET_KEY"]});url=e.get("ALPACA_DATA_BASE_URL","https://data.alpaca.markets").rstrip("/")+"/v2/stocks/bars";rows=[];pages=0
 for off in range(0,len(symbols),50):
  token=None
  while True:
   params={"symbols":",".join(symbols[off:off+50]),"timeframe":"1Day","start":"2026-04-01T00:00:00Z","end":"2026-08-01T00:00:00Z","adjustment":"all","feed":"sip","sort":"asc","limit":10000}
   if token:params["page_token"]=token
   for attempt in range(8):
    r=s.get(url,params=params,timeout=90)
    if r.status_code==429 or r.status_code>=500:time.sleep(min(15,1+2*attempt));continue
    r.raise_for_status();break
   payload=r.json();pages+=1
   for sym,bars in (payload.get("bars") or {}).items():
    for b in bars:rows.append({"symbol":sym,"date":str(b["t"])[:10],"open":b["o"],"high":b["h"],"low":b["l"],"close":b["c"],"volume":b["v"]})
   token=payload.get("next_page_token")
   if not token:break
 d=pd.DataFrame(rows);d.date=pd.to_datetime(d.date);d=d.drop_duplicates(["date","symbol"]).sort_values(["date","symbol"])
 if d.date.max()>pd.Timestamp("2026-07-31") or d.date.min()<pd.Timestamp("2026-04-01"):raise RuntimeError("authorized range breached")
 d.to_parquet(OUT/"daily_all_adjusted_apr_jul.parquet",index=False);(OUT/"daily_pull_report.json").write_text(json.dumps({"status":"passed","symbols":len(symbols),"rows":len(d),"pages":pages,"minimum_date":str(d.date.min().date()),"maximum_date":str(d.date.max().date()),"rows_after_july":int((d.date>pd.Timestamp('2026-07-31')).sum()),"credentials_recorded":False},indent=2)+"\n");print((OUT/"daily_pull_report.json").read_text())
if __name__=="__main__":main()
