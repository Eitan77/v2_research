from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pandas as pd
import requests

import run0003_quote_microtarget as replay


OUT=replay.base.CAM/"artifacts"/"RUN-0004"; RAW=OUT/"quote_windows"
URL="https://data.alpaca.markets/v2/stocks/SOXL/quotes"


def credentials():
    vals=dict(os.environ); env=replay.base.ROOT/".env.local"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                k,v=line.split("=",1); vals.setdefault(k.strip(),v.strip().strip('"').strip("'"))
    if not vals.get("ALPACA_API_KEY_ID") or not vals.get("ALPACA_API_SECRET_KEY"): raise RuntimeError("credentials unavailable")
    return vals["ALPACA_API_KEY_ID"],vals["ALPACA_API_SECRET_KEY"]


def windows(sig):
    raw=[]
    for s in sig.itertuples(index=False): raw.append([s.entry_target,s.entry_target+pd.Timedelta(minutes=5,seconds=2)])
    raw.sort(); merged=[]
    for a,b in raw:
        if merged and a<=merged[-1][1]: merged[-1][1]=max(merged[-1][1],b)
        else: merged.append([a,b])
    if max(b for _,b in merged)>pd.Timestamp("2025-08-01",tz="UTC"): raise RuntimeError("window boundary failure")
    return merged


def fetch(session,key,secret,start,end):
    headers={"APCA-API-KEY-ID":key,"APCA-API-SECRET-KEY":secret}; rows=[]; token=None
    while True:
        params={"start":start.isoformat().replace("+00:00","Z"),"end":end.isoformat().replace("+00:00","Z"),"feed":"sip","limit":10000,"sort":"asc"}
        if token: params["page_token"]=token
        for attempt in range(8):
            r=session.get(URL,params=params,headers=headers,timeout=60)
            if r.status_code==429: time.sleep(min(30,2**attempt)); continue
            r.raise_for_status(); break
        else: raise RuntimeError("repeated rate limit")
        body=r.json(); rows.extend(body.get("quotes",[])); token=body.get("next_page_token")
        if not token: return rows


def pull(sig):
    RAW.mkdir(parents=True,exist_ok=True); key,secret=credentials(); frames=[]; coverage=[]
    with requests.Session() as session:
      for i,(start,end) in enumerate(windows(sig)):
        path=RAW/f"window_{i:04d}.parquet"
        if path.exists(): x=pd.read_parquet(path)
        else:
            rows=fetch(session,key,secret,start,end)
            x=pd.DataFrame({"quote_ts":[r.get("t") for r in rows],"bid_price":[r.get("bp") for r in rows],"ask_price":[r.get("ap") for r in rows],"bid_size":[r.get("bs") for r in rows],"ask_size":[r.get("as") for r in rows]})
            if not x.empty: x["quote_ts"]=pd.to_datetime(x.quote_ts,utc=True)
            x.to_parquet(path,index=False)
        frames.append(x); coverage.append({"window":i,"start":start.isoformat(),"end":end.isoformat(),"rows":len(x)})
        if i%25==0: print(f"windows {i+1}/{len(windows(sig))}",flush=True)
    pd.DataFrame(coverage).to_csv(OUT/"coverage.csv",index=False)
    q=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    if not q.empty:
        q["quote_ts"]=pd.to_datetime(q.quote_ts,utc=True); q=q.drop_duplicates().sort_values("quote_ts")
    return q


def main():
    OUT.mkdir(parents=True,exist_ok=True); sig=replay.signals(); quotes=pull(sig); results={}
    for target in [1,2]:
      for hold in [1,3,5]:
       for extra in [0,1]:
        k=f"t{target}_h{hold}_x{extra}"; x=replay.replay(sig,quotes,target,hold,extra); results[k]=replay.summarize(x)
        if not x.empty:x.assign(config=k).to_csv(OUT/f"ledger_{k}.csv",index=False)
    cov=pd.read_csv(OUT/"coverage.csv")
    report={"signal_events":len(sig),"merged_windows":len(cov),"quote_rows":len(quotes),"zero_row_windows":int((cov.rows==0).sum()),"results":results}
    (OUT/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8"); print(json.dumps(report,indent=2))


if __name__=="__main__":main()
