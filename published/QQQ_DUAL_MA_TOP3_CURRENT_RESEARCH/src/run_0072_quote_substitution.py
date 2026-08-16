from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(Path(__file__).parent))
from run_0033_exit_overlays import base_context
from run_0058_self_financing import quote_frame, simulate, summarize
from run_0071_overextension_substitution import build_schedules

OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0072"
NY=ZoneInfo("America/New_York")
KEYS=["symbol","target_ts","role"]
NAMES=("control","substitute_25","substitute_30","substitute_35")


def context():
    p,score,mask,_,_,_,_=base_context()
    schedules,_=build_schedules(p,score,mask)
    converted={}
    for name in NAMES:
        converted[name]={i:tuple(sorted(str(p.symbols[c]) for c in chosen)) for i,(chosen,_) in schedules[name].items()}
    return p,converted


def roles():
    OUT.mkdir(parents=True,exist_ok=True)
    p,schedules=context(); records={"0930":[],"0940":[]}
    for name,schedule in schedules.items():
        active=tuple()
        for i,target in schedule.items():
            if target==active: continue
            day=pd.Timestamp(p.dates[i])
            for symbol in sorted(set(active)|set(target)):
                for label,clock in (("0930",(9,30)),("0940",(9,40))):
                    records[label].append({"symbol":symbol,"target_ts":pd.Timestamp(datetime.combine(day.date(),time(*clock),tzinfo=NY)).tz_convert("UTC"),"role":"entry_ask_after"})
            active=target
    report={"variants":len(NAMES),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}
    for label,rows in records.items():
        frame=pd.DataFrame(rows).drop_duplicates(KEYS).sort_values(["target_ts","symbol"])
        frame.to_parquet(OUT/f"roles_{label}.parquet",index=False)
        existing=quote_frame(label)
        merged=frame.merge(existing[KEYS],on=KEYS,how="left",indicator=True)
        missing=merged.loc[merged._merge.eq("left_only"),KEYS]
        missing.to_parquet(OUT/f"missing_{label}.parquet",index=False)
        report[f"roles_{label}"]=len(frame);report[f"missing_{label}"]=len(missing)
    (OUT/"roles_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))


def combined(label):
    frames=[quote_frame(label)]
    remote=OUT/f"remote_missing_{label}.parquet"
    if remote.exists(): frames.append(pd.read_parquet(remote))
    q=pd.concat(frames,ignore_index=True);q.target_ts=pd.to_datetime(q.target_ts,utc=True);q.quote_ts=pd.to_datetime(q.quote_ts,utc=True)
    return q.sort_values("quote_ts").drop_duplicates(KEYS)


def replay():
    p,schedules=context(); merged={}
    for label in ("0930","0940"):
        roles=pd.read_parquet(OUT/f"roles_{label}.parquet");roles.target_ts=pd.to_datetime(roles.target_ts,utc=True)
        q=combined(label)
        merged[label]=roles.merge(q[KEYS+["quote_ts","bid_price","ask_price"]],on=KEYS,how="left",validate="one_to_one")
    q30=merged["0930"].copy();q30["date"]=q30.target_ts.dt.tz_convert(NY).dt.tz_localize(None).dt.normalize();q30["reference_mid"]=(q30.bid_price+q30.ask_price)/2
    q40=merged["0940"].copy();q40["date"]=q40.target_ts.dt.tz_convert(NY).dt.tz_localize(None).dt.normalize()
    quotes=q40.merge(q30[["date","symbol","reference_mid"]],on=["date","symbol"],how="left",validate="one_to_one")
    terminal=quotes.symbol.isin(["XLNX","ALXN"])&quotes.bid_price.isna()
    unresolved=quotes[~terminal&(quotes.bid_price.isna()|quotes.ask_price.isna()|quotes.reference_mid.isna())]
    if len(unresolved): raise RuntimeError(f"unresolved quote roles {len(unresolved)}")
    rows=[]
    for name in NAMES:
        for extra in (0.0,1.0,2.0):
            daily,trades,integrity=simulate(p,schedules[name],quotes,mode="change_only",reserve_fraction=.005,extra_bps=extra)
            metrics={"variant":name,"extra_bps":extra,**summarize(daily,trades),**integrity}
            if metrics["minimum_cash"] < -1e-12 or metrics["maximum_gross_to_equity"] > 1+1e-12: raise RuntimeError("cash/exposure failure")
            rows.append(metrics)
            daily.to_parquet(OUT/f"daily_{name}_{int(extra)}bps.parquet",index=False)
    frame=pd.DataFrame(rows);frame.to_csv(OUT/"metrics.csv",index=False)
    control=float(frame[(frame.variant=="control")&(frame.extra_bps==2)].compounded_return.iloc[0])
    if abs(control-16.863855274572497)>1e-8: raise RuntimeError(f"RUN-0058 reproduction failure {control}")
    report={"status":"completed","planned_variants":12,"executed_variants":len(rows),"quote_role_coverage":1.0,
            "maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"metrics":rows}
    (OUT/"report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(frame[["variant","extra_bps","compounded_return","maximum_drawdown","recent12_compounded_return","positive_months","negative_months","worst_month","trade_orders"]].to_string(index=False))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("phase",choices=("roles","replay"));args=parser.parse_args();{"roles":roles,"replay":replay}[args.phase]()
