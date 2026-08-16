from __future__ import annotations
import json
from pathlib import Path
import pandas as pd,requests
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0042"
def main():
 env={}
 for raw in (ROOT/".env.local").read_text().splitlines():
  if "=" in raw and not raw.strip().startswith("#"):k,v=raw.split("=",1);env[k.strip()]=v.strip().strip("\"'")
 url=env.get("ALPACA_DATA_BASE_URL","https://data.alpaca.markets").rstrip("/")+"/v2/stocks/bars";headers={"APCA-API-KEY-ID":env["ALPACA_API_KEY_ID"],"APCA-API-SECRET-KEY":env["ALPACA_API_SECRET_KEY"]};params={"symbols":"QQQ,SPY","timeframe":"1Day","start":"2025-08-11T00:00:00Z","end":"2026-08-11T00:00:00Z","adjustment":"all","feed":"sip","sort":"asc","limit":10000};r=requests.get(url,headers=headers,params=params,timeout=90);r.raise_for_status();rows=[]
 for symbol,bars in (r.json().get("bars") or {}).items():
  for x in bars:rows.append({"symbol":symbol,"date":str(x["t"])[:10],"open":x["o"],"close":x["c"]})
 d=pd.DataFrame(rows);d.date=pd.to_datetime(d.date)
 if set(d.symbol)!={"QQQ","SPY"} or d.date.min()!=pd.Timestamp("2025-08-11") or d.date.max()!=pd.Timestamp("2026-08-10") or (d.date>pd.Timestamp("2026-08-10")).any():raise RuntimeError("benchmark chart coverage failure")
 d.to_parquet(OUT/"chart_benchmarks_last_year.parquet",index=False);report={"status":"passed","symbols":sorted(d.symbol.unique()),"minimum_date":str(d.date.min().date()),"maximum_date":str(d.date.max().date()),"rows":len(d),"adjustment":"all","feed":"sip","rows_after_authorized_end":0,"credentials_recorded":False};(OUT/"chart_benchmarks_report.json").write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report,indent=2))
if __name__=="__main__":main()
