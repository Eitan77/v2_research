from __future__ import annotations

import argparse
from datetime import datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from deep_strategies import build_deep_variants
from repair_strategies import build_repair_variants
from run_suite import _load_or_build_fundamentals
from suite_core import CAMPAIGNS, load_panels, write_json


SHARED = CAMPAIGNS / "CAM-0600" / "artifacts" / "shared"
START = pd.Timestamp("2025-05-01")
END = pd.Timestamp("2026-04-30")
NY = ZoneInfo("America/New_York")


def utc_ts(date: pd.Timestamp, clock: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(pd.Timestamp(date).date(), clock, tzinfo=NY)).tz_convert("UTC")


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--repair",action="store_true"); parser.add_argument("--summary",type=str); parser.add_argument("--prefix",type=str); args=parser.parse_args()
    summary_name = args.summary or ("repair_diagnostic_summary.csv" if args.repair else "deep_diagnostic_summary.csv")
    selected = pd.read_csv(SHARED / summary_name)
    selected = selected[selected["selected_variant"].notna()].copy()
    prefix=args.prefix if args.prefix is not None else ("repair_" if args.repair else "")
    panels = load_panels()
    fundamental, _ = _load_or_build_fundamentals(panels)
    for label, entry_clock in (("0930", time(9,30)), ("0940", time(9,40))):
        rows=[]
        reports=[]
        for record in selected.itertuples(index=False):
            variants=(build_repair_variants if args.repair else build_deep_variants)(str(record.campaign_id),panels,fundamental)
            matches=[v for v in variants if v.variant_id==str(record.selected_variant)]
            if len(matches)!=1: raise RuntimeError(f"variant reconciliation {record.campaign_id}: {len(matches)}")
            v=matches[0]
            executed=np.zeros_like(v.weights)
            if v.execution_lag==1:
                executed[1:]=v.weights[:-1]
            else:
                executed[:]=v.weights
            if (executed < -1e-12).any(): raise RuntimeError(f"direct short in {record.campaign_id}")
            p=v.panel
            changes=0
            if v.holding=="open_to_close":
                for i,date in enumerate(p.dates):
                    date=pd.Timestamp(date).normalize()
                    if date<START or date>END: continue
                    for col in np.flatnonzero(executed[i]>1e-12):
                        symbol=str(p.symbols[col]); weight=float(executed[i,col])
                        rows.append({"campaign_id":record.campaign_id,"variant_id":v.variant_id,"holding":v.holding,"session_date":date,"symbol":symbol,"side":"buy","delta_weight":weight,"target_ts":utc_ts(date,entry_clock),"role":"entry_ask_after","reference_open":float(p.raw_open[i,col])})
                        rows.append({"campaign_id":record.campaign_id,"variant_id":v.variant_id,"holding":v.holding,"session_date":date,"symbol":symbol,"side":"sell","delta_weight":weight,"target_ts":utc_ts(date,time(16,0)),"role":"exit_bid_before","reference_open":float(p.raw_open[i,col])})
                        changes+=2
            else:
                previous=np.zeros(p.n_symbols)
                for i,date in enumerate(p.dates):
                    date=pd.Timestamp(date).normalize()
                    current=executed[i]
                    if date<START:
                        previous=current.copy(); continue
                    if date>END: break
                    delta=current-previous
                    for col in np.flatnonzero(np.abs(delta)>1e-8):
                        side="buy" if delta[col]>0 else "sell"
                        role="entry_ask_after" if side=="buy" else "exit_bid_after"
                        rows.append({"campaign_id":record.campaign_id,"variant_id":v.variant_id,"holding":v.holding,"session_date":date,"symbol":str(p.symbols[col]),"side":side,"delta_weight":float(abs(delta[col])),"target_ts":utc_ts(date,entry_clock),"role":role,"reference_open":float(p.raw_open[i,col])})
                        changes+=1
                    previous=current.copy()
            reports.append({"campaign_id":record.campaign_id,"variant_id":v.variant_id,"holding":v.holding,"trade_roles":changes,"maximum_gross":float(executed.sum(axis=1).max())})
        ledger=pd.DataFrame(rows)
        if ledger.empty or (pd.to_datetime(ledger.target_ts,utc=True)>=pd.Timestamp("2026-05-01",tz="UTC")).any(): raise RuntimeError("empty or holdout-crossing ledger")
        ledger.to_parquet(SHARED/f"{prefix}target_change_trades_{label}.parquet",index=False)
        roles=ledger[["symbol","target_ts","role"]].drop_duplicates()
        roles.to_parquet(SHARED/f"{prefix}target_change_roles_{label}.parquet",index=False)
        write_json(SHARED/f"{prefix}target_change_ledger_{label}_report.json",{"status":"passed","campaigns":int(ledger.campaign_id.nunique()),"trade_rows":int(len(ledger)),"roles":int(len(roles)),"symbols":int(ledger.symbol.nunique()),"minimum_session":str(ledger.session_date.min().date()),"maximum_session":str(ledger.session_date.max().date()),"holdout_rows_loaded":0,"reports":reports})
        print(label,len(ledger),len(roles),ledger.campaign_id.nunique())


if __name__ == "__main__":
    main()
