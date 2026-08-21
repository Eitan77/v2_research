from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

import run0003_quote_microtarget as replay
import run0004_pull_and_replay as pull


OUT=replay.base.CAM/"artifacts"/"RUN-0006"


def inputs():
    all_sig=replay.signals(); sig=all_sig[(all_sig.date>=pd.Timestamp("2025-05-01"))&(all_sig.date<=pd.Timestamp("2025-05-31"))].copy()
    wins=pull.windows(all_sig); ids=[i for i,(a,b) in enumerate(wins) if a<pd.Timestamp("2025-06-01",tz="UTC")]
    paths=[pull.RAW/f"window_{i:04d}.parquet" for i in ids]
    if any(not p.exists() for p in paths): raise RuntimeError("incomplete May cache")
    q=pd.concat([pd.read_parquet(p) for p in paths],ignore_index=True); q["quote_ts"]=pd.to_datetime(q.quote_ts,utc=True)
    return sig,q.drop_duplicates().sort_values("quote_ts")


def run(sig,q,target_bp,hold,stop_slip):
    rows=[]; last_exit=np.datetime64("1970-01-01")
    for date,sday in sig.groupby("date",sort=True):
      d=q[q.quote_ts.dt.date==pd.Timestamp(date).date()].reset_index(drop=True)
      ts=d.quote_ts.to_numpy(dtype="datetime64[ns]"); bids=d.bid_price.to_numpy(); asks=d.ask_price.to_numpy()
      for s in sday.itertuples(index=False):
        entry_target=np.datetime64(s.entry_target.to_datetime64())
        if entry_target<=last_exit: continue
        ei=int(np.searchsorted(ts,entry_target,"left"))
        if ei>=len(d) or ts[ei]>entry_target+np.timedelta64(2,"s"): continue
        entry=float(asks[ei]); limit=math.ceil((entry*(1+target_bp/1e4)-1e-12)*100)/100
        distance=limit-entry; stop=entry-distance; deadline=entry_target+np.timedelta64(hold,"m")
        zi=min(int(np.searchsorted(ts,deadline,"left")),len(d)-1); outcome="timeout"; xi=zi
        for j in range(ei+1,zi+1):
          if bids[j]>=limit: outcome="target"; xi=j; break
          if bids[j]<=stop: outcome="stop"; xi=j; break
        if outcome=="target": exit_px=limit
        elif outcome=="stop": exit_px=float(bids[xi])*(1-stop_slip/1e4)
        else: exit_px=float(bids[xi])*(1-stop_slip/1e4)
        rows.append({"date":date,"entry_ts":d.loc[ei,"quote_ts"],"exit_ts":d.loc[xi,"quote_ts"],"entry":entry,"limit":limit,"stop":stop,
                     "effective_distance_bp":distance/entry*1e4,"entry_spread_bp":(asks[ei]/bids[ei]-1)*1e4,"outcome":outcome,"exit":exit_px,"net_return":exit_px/entry-1})
        last_exit=ts[xi]
    return pd.DataFrame(rows)


def summary(x):
    monthly=x.groupby("date").net_return.sum().resample("ME").sum(); eq=1+x.groupby("date").net_return.sum().cumsum(); dd=((eq.cummax()-eq)/eq.cummax()).max()
    return {"trades":len(x),"net_return":float(x.net_return.sum()),"mean_trade_bp":float(x.net_return.mean()*1e4),"target_rate":float((x.outcome=="target").mean()),
            "stop_rate":float((x.outcome=="stop").mean()),"timeout_rate":float((x.outcome=="timeout").mean()),"median_hold_ms":float((x.exit_ts-x.entry_ts).dt.total_seconds().median()*1000),
            "mean_distance_bp":float(x.effective_distance_bp.mean()),"mean_entry_spread_bp":float(x.entry_spread_bp.mean()),"max_drawdown":float(dd),"monthly":{str(k.date()):float(v) for k,v in monthly.items()}}


def main():
    OUT.mkdir(parents=True,exist_ok=True); sig,q=inputs(); results={}
    for target in [1,2]:
      for hold in [1,3,5]:
       for slip in [0,1]:
        k=f"t{target}_h{hold}_s{slip}"; x=run(sig,q,target,hold,slip); results[k]=summary(x); x.assign(config=k).to_csv(OUT/f"ledger_{k}.csv",index=False)
    report={"signal_events":len(sig),"quote_rows":len(q),"results":results}; (OUT/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8"); print(json.dumps(report,indent=2))


if __name__=="__main__":main()
