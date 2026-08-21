from __future__ import annotations

import json
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

import run0001_bar_microtarget as base


OUT = base.CAM / "artifacts" / "RUN-0003"
QUOTE_GLOB = "D:/AlgoResearch/data/raw/alpaca/market/stocks/quotes_sip/schema_v1/session_date=*/quotes.parquet"


def load_quotes() -> pd.DataFrame:
    con=duckdb.connect()
    q=f"""
      select quote_ts, bid_price, ask_price, bid_size, ask_size
      from read_parquet('{QUOTE_GLOB}', hive_partitioning=true)
      where session_date between '2025-05-01' and '2025-07-31'
        and symbol='SOXL' and bid_price > 0 and ask_price >= bid_price
      order by quote_ts
    """
    x=con.execute(q).fetchdf(); con.close(); x["quote_ts"]=pd.to_datetime(x.quote_ts,utc=True)
    return x.drop_duplicates(["quote_ts","bid_price","ask_price","bid_size","ask_size"])


def signals() -> pd.DataFrame:
    x=base.load(); y=x[(x.green_bp>=20)&(x.rvol>=2)&(x.hhmm>="09:35")&(x.hhmm<="15:45")].copy()
    y["entry_target"]=y.ts+pd.Timedelta(minutes=1)
    return y[["date","ts","entry_target","green_bp","rvol"]].reset_index(drop=True)


def first_index_at_or_after(ts: np.ndarray, target: np.datetime64) -> int:
    return int(np.searchsorted(ts,target,side="left"))


def replay(sig: pd.DataFrame, quotes: pd.DataFrame, target_bp: int, hold_min: int, extra_bp: int) -> pd.DataFrame:
    rows=[]; last_exit=np.datetime64("1970-01-01")
    for date,sday in sig.groupby("date",sort=True):
        q=quotes[quotes.quote_ts.dt.date==pd.Timestamp(date).date()].reset_index(drop=True)
        if q.empty: continue
        ts=q.quote_ts.to_numpy(dtype="datetime64[ns]"); bids=q.bid_price.to_numpy(); asks=q.ask_price.to_numpy()
        bs=q.bid_size.to_numpy(); ass=q.ask_size.to_numpy()
        for s in sday.itertuples(index=False):
            target_ts=np.datetime64(s.entry_target.to_datetime64())
            if target_ts<=last_exit: continue
            ei=first_index_at_or_after(ts,target_ts)
            if ei>=len(q) or ts[ei]>target_ts+np.timedelta64(2,"s"): continue
            entry=float(asks[ei])*(1+extra_bp/1e4)
            limit=math.ceil((entry*(1+target_bp/1e4)-1e-12)*100)/100
            deadline=target_ts+np.timedelta64(hold_min,"m")
            zi=int(np.searchsorted(ts,deadline,side="left")); zi=min(zi,len(q)-1)
            path=np.flatnonzero(bids[ei+1:zi+1]>=limit)
            hit=len(path)>0
            xi=ei+1+int(path[0]) if hit else zi
            if not hit and (ts[xi]>deadline+np.timedelta64(2,"s")): continue
            exit_px=limit if hit else float(bids[xi])*(1-extra_bp/1e4)
            shares=max(1,int(2000//entry))
            rows.append({"date":date,"signal_ts":s.ts,"entry_quote_ts":q.loc[ei,"quote_ts"],"exit_quote_ts":q.loc[xi,"quote_ts"],
                         "entry_ask":entry,"raw_entry_ask":float(asks[ei]),"entry_ask_size_raw":int(ass[ei]),
                         "limit":limit,"effective_target_bp":(limit/entry-1)*1e4,"target_hit":hit,
                         "exit":exit_px,"exit_bid_size_raw":int(bs[xi]),"shares_2000":shares,"net_return":exit_px/entry-1})
            last_exit=ts[xi]
    return pd.DataFrame(rows)


def summarize(x: pd.DataFrame) -> dict:
    if x.empty:return {"trades":0}
    daily=x.groupby("date").net_return.sum(); monthly=daily.resample("ME").sum(); eq=1+daily.cumsum(); dd=((eq.cummax()-eq)/eq.cummax()).max()
    losers=x[~x.target_hit]
    return {"trades":len(x),"net_return":float(x.net_return.sum()),"mean_trade_bp":float(x.net_return.mean()*1e4),
            "target_fill_rate":float(x.target_hit.mean()),"mean_effective_target_bp":float(x.effective_target_bp.mean()),
            "forced_exit_mean_bp":float(losers.net_return.mean()*1e4) if len(losers) else None,"positive_months":int((monthly>0).sum()),
            "monthly":{str(k.date()):float(v) for k,v in monthly.items()},"max_drawdown":float(dd),
            "min_entry_ask_size_raw":int(x.entry_ask_size_raw.min()),"min_exit_bid_size_raw":int(x.exit_bid_size_raw.min())}


def main() -> None:
    OUT.mkdir(parents=True,exist_ok=True); sig=signals(); quotes=load_quotes(); results={}; ledgers=[]
    for target in [1,2]:
      for hold in [1,3,5]:
       for extra in [0,1]:
        key=f"t{target}_h{hold}_x{extra}"; x=replay(sig,quotes,target,hold,extra); results[key]=summarize(x)
        if not x.empty: x.assign(config=key).to_csv(OUT/f"ledger_{key}.csv",index=False)
    report={"signal_events":len(sig),"quote_rows":len(quotes),"quote_min":str(quotes.quote_ts.min()),"quote_max":str(quotes.quote_ts.max()),"results":results}
    (OUT/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8"); print(json.dumps(report,indent=2))


if __name__=="__main__":main()
