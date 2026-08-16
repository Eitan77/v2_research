from __future__ import annotations
import json
from pathlib import Path
import pandas as pd,requests
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0041"
def main():
 env={}
 for raw in (ROOT/".env.local").read_text().splitlines():
  if "=" in raw and not raw.strip().startswith("#"):k,v=raw.split("=",1);env[k.strip()]=v.strip().strip("\"'")
 url=env.get("ALPACA_DATA_BASE_URL","https://data.alpaca.markets").rstrip("/")+"/v2/stocks/bars"
 headers={"APCA-API-KEY-ID":env["ALPACA_API_KEY_ID"],"APCA-API-SECRET-KEY":env["ALPACA_API_SECRET_KEY"]}
 params={"symbols":"QQQ","timeframe":"1Day","start":"2026-05-01T00:00:00Z","end":"2026-08-01T00:00:00Z","adjustment":"all","feed":"sip","sort":"asc","limit":10000}
 r=requests.get(url,headers=headers,params=params,timeout=90);r.raise_for_status();bars=r.json().get("bars",{}).get("QQQ",[]);d=pd.DataFrame([{"date":str(x["t"])[:10],"open":x["o"],"close":x["c"]} for x in bars]);d.date=pd.to_datetime(d.date)
 if d.empty or d.date.max()>pd.Timestamp("2026-07-31"):raise RuntimeError("benchmark authorization or coverage failure")
 d.to_parquet(OUT/"qqq_benchmark_bars.parquet",index=False);monthly={str(k):float(100*(g.close.iloc[-1]/g.open.iloc[0]-1)) for k,g in d.groupby(d.date.dt.to_period("M"))};report={"source":"Alpaca SIP daily adjustment=all","method":"first_session_open_to_last_session_close","monthly_return_pct":monthly,"may_july_return_pct":float(100*(d.close.iloc[-1]/d.open.iloc[0]-1)),"minimum_date":str(d.date.min().date()),"maximum_date":str(d.date.max().date()),"rows_after_july":int((d.date>pd.Timestamp('2026-07-31')).sum()),"credentials_recorded":False};(OUT/"qqq_benchmark.json").write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report,indent=2))
if __name__=="__main__":main()
