from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; SH=CAM/"CAM-0600"/"artifacts"/"shared"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0027"; IDS=["CAM-0600","CAM-0621","CAM-0624","CAM-0618"]; sys.path.insert(0,str(CAM/"CAM-0600"/"src"))
from deep_strategies import build_deep_variants
from repair_strategies import build_repair_variants
from run_suite import _load_or_build_fundamentals
from suite_core import load_panels

def main():
 OUT.mkdir(parents=True,exist_ok=True); base=pd.read_csv(SH/"split_repaired_diagnostic_summary.csv").set_index("campaign_id"); repair=pd.read_csv(SH/"split_repaired_repair_diagnostic_summary.csv").set_index("campaign_id"); replay=pd.read_parquet(SH/"split_repaired_quote_replay_0940.parquet"); panels=load_panels(); fundamentals,_=_load_or_build_fundamentals(panels); details=[]; sleeve_rows=[]
 for cid in IDS:
  row=(repair if cid=="CAM-0621" else base).loc[cid]; variant_id=str(row.selected_variant); builder=build_repair_variants if cid=="CAM-0621" else build_deep_variants; variant=next(v for v in builder(cid,panels,fundamentals) if v.variant_id==variant_id); executed=np.zeros_like(variant.weights); executed[1:]=variant.weights[:-1]; returns=variant.panel.open_to_next_open_return.copy(); returns[-1]=variant.panel.open_to_close_return[-1]; dates=pd.DatetimeIndex(variant.panel.dates); mask=(dates>=pd.Timestamp("2025-05-01"))&(dates<=pd.Timestamp("2026-04-30")); gross=np.nansum(executed[mask]*np.nan_to_num(returns[mask],nan=0),axis=0); g=replay[(replay.campaign_id==cid)&replay.effective_complete].copy(); buy=g.side.eq("buy"); g["adjustment"]=np.where(buy,g.delta_weight*(g.ask_price/g.reference_mid-1),g.delta_weight*(1-g.bid_price/g.reference_mid))+g.delta_weight*2/10000; adjustment=g.groupby("symbol").adjustment.sum(); sym=pd.DataFrame({"symbol":variant.panel.symbols.astype(str),"gross_pnl":gross}); sym["execution_adjustment"]=sym.symbol.map(adjustment).fillna(0); sym["net_pnl"]=(sym.gross_pnl-sym.execution_adjustment)*.25; sym.insert(0,"campaign_id",cid); details.append(sym); sleeve_rows.append({"campaign_id":cid,"variant":variant_id,"weighted_net":float(sym.net_pnl.sum()),"symbols":int((sym.net_pnl.abs()>1e-12).sum())})
 detail=pd.concat(details,ignore_index=True); combined=detail.groupby("symbol",as_index=False).agg(gross_pnl=("gross_pnl",lambda x:float(x.sum()*.25)),execution_adjustment=("execution_adjustment",lambda x:float(x.sum()*.25)),net_pnl=("net_pnl","sum")).sort_values("net_pnl",ascending=False); positive=combined.net_pnl.clip(lower=0); total=float(combined.net_pnl.sum()); report={"status":"completed","run_id":"RUN-0027","sleeves":sleeve_rows,"net_simple_return":total,"positive_symbols":int((combined.net_pnl>0).sum()),"negative_symbols":int((combined.net_pnl<0).sum()),"top_symbol":str(combined.symbol.iloc[0]),"top_symbol_net":float(combined.net_pnl.iloc[0]),"top_symbol_positive_share":float(positive.iloc[0]/positive.sum()),"top5_symbol_positive_share":float(positive.head(5).sum()/positive.sum()),"leave_top_symbol_out_net":float(total-combined.net_pnl.iloc[0]),"leave_top5_symbols_out_net":float(total-combined.net_pnl.head(5).sum()),"top10":combined.head(10).to_dict("records"),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Exact execution-adjusted development symbol attribution."}
 expected=0.3996272220870408
 if abs(total-expected)>1e-8: raise RuntimeError(f"ensemble attribution mismatch {total-expected}")
 detail.to_csv(OUT/"sleeve_symbol_detail.csv",index=False); combined.to_csv(OUT/"combined_symbol_attribution.csv",index=False); (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); path=CAM/"CAM-0625"/"runs"/"RUN-0027.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="completed"; run["result"]=report; run["decision"]="Use symbol leave-outs as a hard qualification of the final recent-regime lead; no promotion."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":"RUN-0027","event":"completed","result":report,"holdout_rows_loaded":0})+"\n")
 print(json.dumps(report,indent=2))
if __name__=="__main__": main()
