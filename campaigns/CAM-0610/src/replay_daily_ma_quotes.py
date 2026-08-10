from __future__ import annotations

import argparse, json, sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0600" / "src"))
from baseline_strategies import moving_average
from deep_strategies import active_trend_rank
from suite_core import CAMPAIGNS, load_panels

OUT = CAMPAIGNS / "CAM-0610" / "artifacts" / "RUN-0025"
RUN = CAMPAIGNS / "CAM-0610" / "runs" / "RUN-0025.yaml"
VARIANT = "sp500__ma200__daily__top10__momentum"
GATED = True
EXTRA_QUOTE_DIRS = []
BAR_RUN = "RUN-0024"
START, END = pd.Timestamp("2025-05-01"), pd.Timestamp("2026-04-30")
NY = ZoneInfo("America/New_York")

def target(date, clock):
    h, m = map(int, clock.split(":"))
    return pd.Timestamp(datetime.combine(pd.Timestamp(date).date(), time(h,m), tzinfo=NY)).tz_convert("UTC")

def weights_and_panel():
    p = load_panels()["sp500"]
    condition = p.adj_close > moving_average(p, 200) if GATED else np.ones_like(p.adj_close, dtype=bool)
    w = active_trend_rank(p, condition, np.arange(p.n_dates), 10, "momentum")
    return p, w

def ledgers():
    OUT.mkdir(parents=True, exist_ok=True); p, w = weights_and_panel()
    executed = np.zeros_like(w); executed[1:] = w[:-1]
    for clock in ("09:30", "09:40"):
        rows=[]; previous=np.zeros(p.n_symbols)
        for i,date in enumerate(p.dates):
            date=pd.Timestamp(date).normalize(); current=executed[i]
            if date < START: previous=current.copy(); continue
            if date > END: break
            delta=current-previous
            for col in np.flatnonzero(np.abs(delta)>1e-8):
                side="buy" if delta[col]>0 else "sell"
                rows.append({"session_date":date,"symbol":str(p.symbols[col]),"side":side,
                             "delta_weight":float(abs(delta[col])),"target_ts":target(date,clock),
                             "role":"entry_ask_after" if side=="buy" else "exit_bid_after"})
            previous=current.copy()
        d=pd.DataFrame(rows); label=clock.replace(":","")
        d.to_parquet(OUT/f"ledger_{label}.parquet",index=False)
        d[["symbol","target_ts","role"]].drop_duplicates().to_parquet(OUT/f"roles_{label}.parquet",index=False)
        print(label,len(d),d.session_date.nunique(),int(d.side.eq('buy').sum()))

def dd(x):
    e=1+x.cumsum(); return float(((e.cummax()-e)/e.cummax()).max())

def replay():
    rep={}
    for label in ("0930","0940"):
        d=pd.read_parquet(OUT/f"ledger_{label}.parquet"); qs=[]
        for directory in [OUT, *EXTRA_QUOTE_DIRS]:
            for sec in (5,30,120):
                path=directory/f"quotes_{label}_{sec}s.parquet"
                if path.exists(): q=pd.read_parquet(path); q["priority"]=sec; qs.append(q)
        q=pd.concat(qs,ignore_index=True).sort_values("priority").drop_duplicates(["symbol","target_ts","role"])
        d.target_ts=pd.to_datetime(d.target_ts,utc=True); q.target_ts=pd.to_datetime(q.target_ts,utc=True)
        z=d.merge(q[["symbol","target_ts","role","bid_price","ask_price"]],on=["symbol","target_ts","role"],how="left",validate="one_to_one")
        z["complete"]=z.bid_price.notna()&z.ask_price.notna()&(z.bid_price>0)&(z.ask_price>=z.bid_price); rep[label]=z
    ref=rep["0930"].copy(); ref["reference_mid"]=(ref.bid_price+ref.ask_price)/2
    ref=ref[["session_date","symbol","side","reference_mid"]]
    rep["0940"]=rep["0940"].merge(ref,on=["session_date","symbol","side"],how="left",validate="one_to_one")
    rep["0930"]["reference_mid"]=(rep["0930"].bid_price+rep["0930"].ask_price)/2
    safe=f"{VARIANT}__cost_2bps"
    bar=pd.read_parquet(CAMPAIGNS/"CAM-0610"/"artifacts"/BAR_RUN/"variants"/safe/"daily.parquet")
    bar.date=pd.to_datetime(bar.date); bar=bar[(bar.date>=START)&(bar.date<=END)].set_index("date")
    rows=[]
    for label,g in rep.items():
        g["effective"]=g.complete&g.reference_mid.notna()&(g.reference_mid>0); c=g[g.effective].copy()
        for extra in (0.,1.,2.,5.):
            c["adj"]=np.where(c.side.eq("buy"),c.delta_weight*(c.ask_price/c.reference_mid-1),c.delta_weight*(1-c.bid_price/c.reference_mid))+c.delta_weight*extra/10000
            daily=bar.gross_pnl.subtract(c.groupby(pd.to_datetime(c.session_date)).adj.sum(),fill_value=0).sort_index(); monthly=daily.groupby(daily.index.to_period("M")).sum()
            rows.append({"clock":label,"extra_adverse_bps_per_side":extra,"net_simple_return":float(daily.sum()),"maximum_drawdown":dd(daily),
                         "role_coverage":float(g.effective.mean()),"entry_roles":int(c.side.eq('buy').sum()),"trade_sessions":int(pd.to_datetime(c.session_date).nunique()),
                         "trade_session_fraction":float(pd.to_datetime(c.session_date).nunique()/len(bar)),"positive_months":int((monthly>0).sum()),"negative_months":int((monthly<0).sum()),
                         "monthly_average":float(monthly.mean()),"monthly_median":float(monthly.median()),"worst_month":float(monthly.min()),"best_month":float(monthly.max())})
            daily.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/f"daily_{label}_{extra:g}bps.parquet",index=False)
    m=pd.DataFrame(rows); m.to_csv(OUT/"quote_metrics.csv",index=False)
    run_id=yaml.safe_load(RUN.read_text())["run_id"]
    report={"status":"completed","run_id":run_id,"variant":VARIANT,"metrics":json.loads(m.to_json(orient="records")),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin":False,"direct_short":False}
    (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n")
    r=yaml.safe_load(RUN.read_text()); r["status"]="completed"; r["result"]=report; r["decision"]="Require cadence, concentration, and limit-order audit before any paper-trading recommendation."; RUN.write_text(yaml.safe_dump(r,sort_keys=False))
    print(m.to_string(index=False))

if __name__=="__main__":
    a=argparse.ArgumentParser(); a.add_argument("phase",choices=["ledgers","replay"]); x=a.parse_args(); ledgers() if x.phase=="ledgers" else replay()
