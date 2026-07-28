"""SOXL/SOXS-only 1-minute structural refinement screen."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


def load(catalog: Path) -> pd.DataFrame:
    con = duckdb.connect(str(catalog), read_only=True)
    try:
        d = con.execute("""
            select timestamp, symbol, open, high, low, close, volume
            from bars_1m
            where symbol in ('SOXL','SOXS') and timestamp < '2026-06-01'
            order by timestamp, symbol
        """).fetchdf()
    finally:
        con.close()
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True)
    d["ny"] = d["timestamp"].dt.tz_convert("America/New_York")
    d["date"] = d["ny"].dt.strftime("%Y-%m-%d")
    d["minute"] = d["ny"].dt.hour * 60 + d["ny"].dt.minute
    d = d[(d.minute >= 570) & (d.minute < 960)].copy()
    d["dollar"] = d.close * d.volume
    d["svwap"] = d.groupby(["symbol", "date"]).dollar.cumsum() / d.groupby(["symbol", "date"]).volume.cumsum()
    g = d.groupby("symbol", sort=False)
    d["ret3"] = g.close.pct_change(3); d["ret5"] = g.close.pct_change(5); d["ret15"] = g.close.pct_change(15)
    d["prev_close"] = g.close.shift(1); d["prev_vwap"] = d.groupby(["symbol", "date"]).svwap.shift(1)
    d["vol_rel"] = d.volume / g.volume.transform(lambda x: x.rolling(20, min_periods=10).mean())
    p = d.pivot(index="timestamp", columns="symbol", values="close").sort_index()
    p["pair_ret15"] = p["SOXL"].pct_change(15) - p["SOXS"].pct_change(15)
    p["pair_z15"] = (p.pair_ret15 - p.pair_ret15.rolling(120, min_periods=60).mean()) / p.pair_ret15.rolling(120, min_periods=60).std()
    d = d.merge(p[["pair_ret15", "pair_z15"]], left_on="timestamp", right_index=True, how="left")
    orb = d[d.minute < 585].groupby("date").agg(orb_high=("high", "max"), orb_low=("low", "min"))
    return d.merge(orb, left_on="date", right_index=True, how="left").sort_values(["timestamp", "symbol"]).reset_index(drop=True)


def make_signals(d: pd.DataFrame, family: str) -> pd.DataFrame:
    after = d.minute.between(580, 930)
    cross_up = (d.close > d.svwap) & (d.prev_close <= d.prev_vwap)
    if family == "vwap_momentum":
        m = after & cross_up & (d.ret5 > .002) & (d.vol_rel > 1.2) & (((d.symbol == "SOXL") & (d.pair_ret15 > 0)) | ((d.symbol == "SOXS") & (d.pair_ret15 < 0)))
    elif family == "orb_momentum":
        m = d.minute.between(585, 930) & (d.close > d.orb_high) & (d.close > d.svwap) & (d.vol_rel > 1.2) & (((d.symbol == "SOXL") & (d.pair_ret15 > 0)) | ((d.symbol == "SOXS") & (d.pair_ret15 < 0)))
    elif family == "pair_reversion":
        m = after & (d.pair_z15.abs() > 1.5) & (d.ret3 < -.001) & (d.close > d.prev_close) & (d.vol_rel > 1.0) & (((d.symbol == "SOXL") & (d.pair_z15 < -1.5)) | ((d.symbol == "SOXS") & (d.pair_z15 > 1.5)))
    else: raise ValueError(family)
    s = d.loc[m, ["timestamp","symbol","date","minute"]].copy()
    return s.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def exits(s: pd.DataFrame, d: pd.DataFrame, hold: int, target: float, stop: float) -> pd.DataFrame:
    by = {x: y.sort_values("timestamp").reset_index(drop=True) for x,y in d.groupby("symbol")}
    maps = {x: {t:i for i,t in enumerate(y.timestamp)} for x,y in by.items()}
    out=[]
    for r in s.itertuples(index=False):
        g=by[r.symbol]; i=maps[r.symbol].get(r.timestamp)
        if i is None or i+1>=len(g): continue
        en=g.iloc[i+1]; ep=float(en.open); tp=ep*(1+target); sp=ep*(1-stop); end=min(i+hold,len(g)-1); ex=g.iloc[end]; xp=float(ex.close); xt=ex.timestamp
        reason="time"
        for j in range(i+1,end+1):
            b=g.iloc[j]
            if float(b.low)<=sp: xp=sp; xt=b.timestamp; reason="stop"; break
            if float(b.high)>=tp: xp=tp; xt=b.timestamp; reason="target"; break
        local=en.timestamp.tz_convert("America/New_York")
        out.append({"entry_ts":en.timestamp,"exit_ts":xt,"symbol":r.symbol,"entry_minute":local.hour*60+local.minute,"gross_return":xp/ep-1,"reason":reason})
    return pd.DataFrame(out)


def sim(t: pd.DataFrame, cost: float, total_days: int) -> dict:
    if t.empty: return {"trades":0,"hard_gate":False,"mean_monthly_net_pct":0.0}
    t=t.sort_values(["entry_ts","exit_ts"]); acc=[]; free=pd.Timestamp.min.tz_localize("UTC")
    for r in t.itertuples(index=False):
        if r.entry_ts>=free: acc.append(r._asdict()); free=r.exit_ts
    e=pd.DataFrame(acc); e["cost_side"]=np.where(e.entry_minute<600,20.,cost); e["net"]=e.gross_return-2*e.cost_side/10000
    e["month"]=e.entry_ts.dt.tz_convert("America/New_York").dt.strftime("%Y-%m"); e["year"]=e.entry_ts.dt.year
    month=e.groupby("month").net.apply(lambda x:(1+x).prod()-1); year=e.groupby("year").net.apply(lambda x:(1+x).prod()-1)
    day=e.groupby(e.entry_ts.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")).net.apply(lambda x:(1+x).prod()-1); eq=np.cumprod(np.clip(1+e.net.to_numpy(float),1e-12,None)); dd=eq/np.maximum.accumulate(eq)-1
    out={"trades":len(e),"trades_per_trading_day":len(e)/max(total_days,1),"mean_active_day_net_pct":day.mean()*100,"mean_monthly_net_pct":month.mean()*100,"median_monthly_net_pct":month.median()*100,"worst_month_net_pct":month.min()*100,"max_drawdown":dd.min(),"years_tested":len(year),"positive_years":int((year>0).sum()),"worst_year_return":year.min(),"win_rate":(e.net>0).mean()}
    out["frequency_gate"]=out["trades_per_trading_day"]>=.5; out["hard_gate"]=bool(out["frequency_gate"] and out["mean_monthly_net_pct"]>=10 and out["max_drawdown"]>=-.35 and out["worst_year_return"]>=-.35 and out["years_tested"]>=2); return out


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--catalog",default="D:/AlgoResearch/data/catalog.duckdb",type=Path); ap.add_argument("--out",required=True,type=Path); a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    d=load(a.catalog); rows=[]; families=["vwap_momentum","orb_momentum","pair_reversion"]
    for fam in families:
        s=make_signals(d,fam)
        for hold in [3,5,10,20]:
            for target,stop in [(.005,.003),(.01,.005),(.02,.008)]:
                tr=exits(s,d,hold,target,stop)
                for cost in [0,5,10,15,20]:
                    m=sim(tr,cost,d.date.nunique()); rows.append({"family":fam,"hold_minutes":hold,"target":target,"stop":stop,"later_cost_bps":cost,**m})
    out=pd.DataFrame(rows).sort_values(["hard_gate","mean_monthly_net_pct"],ascending=False); out.to_csv(a.out/"soxl_soxs_1m_metrics.csv",index=False); out.groupby("later_cost_bps",as_index=False).head(5).to_csv(a.out/"soxl_soxs_1m_top_by_cost.csv",index=False)
    summary={"rows":len(d),"symbols":sorted(d.symbol.unique().tolist()),"specs":len(families)*4*3,"metric_rows":len(out),"hard_gate_rows":int(out.hard_gate.sum()),"best_mean_monthly_net_pct":float(out.mean_monthly_net_pct.max()),"best_mean_active_day_net_pct":float(out.mean_active_day_net_pct.max()),"cutoff_exclusive":"2026-06-01","quote_path_eligible":bool(out.hard_gate.any())}; (a.out/"soxl_soxs_1m_summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8"); (a.out/"soxl_soxs_1m_review.md").write_text("# SOXL/SOXS 1-minute refinement\n\n"+json.dumps(summary,indent=2)+"\n\n```text\n"+out.groupby("later_cost_bps",as_index=False).head(1).to_string(index=False)+"\n```\n",encoding="utf-8"); print(json.dumps(summary,indent=2))

if __name__=="__main__": main()
