from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; SH=CAM/"CAM-0600"/"artifacts"/"shared"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0026"; IDS=["CAM-0612","CAM-0611","CAM-0610","CAM-0623","CAM-0600"]; sys.path.insert(0,str(CAM/"CAM-0600"/"src"))
from deep_strategies import build_deep_variants
from run_suite import _load_or_build_fundamentals
from suite_core import load_panels

def main():
 OUT.mkdir(parents=True,exist_ok=True); selected=pd.read_csv(SH/"split_repaired_diagnostic_summary.csv").set_index("campaign_id"); replay=pd.read_parquet(SH/"split_repaired_quote_replay_0940.parquet"); replay.session_date=pd.to_datetime(replay.session_date); panels=load_panels(); fundamentals,_=_load_or_build_fundamentals(panels); rows=[]; details=[]
 for cid in IDS:
  variant_id=str(selected.loc[cid,"selected_variant"]); variant=next(v for v in build_deep_variants(cid,panels,fundamentals) if v.variant_id==variant_id); executed=np.zeros_like(variant.weights); executed[1:]=variant.weights[:-1]; dates=pd.DatetimeIndex(variant.panel.dates); returns=variant.panel.open_to_next_open_return.copy(); returns[-1]=variant.panel.open_to_close_return[-1]; mask=(dates>=pd.Timestamp("2025-05-01"))&(dates<=pd.Timestamp("2026-04-30")); gross=np.nansum(executed[mask]*np.nan_to_num(returns[mask],nan=0),axis=0); g=replay[(replay.campaign_id==cid)&replay.effective_complete].copy(); buy=g.side.eq("buy"); g["execution_adjustment"]=np.where(buy,g.delta_weight*(g.ask_price/g.reference_mid-1),g.delta_weight*(1-g.bid_price/g.reference_mid))+g.delta_weight*2/10000; adjustment=g.groupby("symbol").execution_adjustment.sum(); sym=pd.DataFrame({"symbol":variant.panel.symbols.astype(str),"gross_pnl":gross}); sym["execution_adjustment"]=sym.symbol.map(adjustment).fillna(0); sym["net_pnl"]=sym.gross_pnl-sym.execution_adjustment; sym=sym.sort_values("net_pnl",ascending=False); positive=sym.net_pnl.clip(lower=0); expected=pd.read_csv(CAM/cid/"artifacts"/"RUN-0023"/"quote_metrics_0940.csv"); expected=float(expected[expected.extra_slippage_bps_per_side==2].net_simple_return.iloc[0]); if_diff=float(sym.net_pnl.sum()-expected)
  if abs(if_diff)>1e-8: raise RuntimeError(f"{cid} attribution mismatch {if_diff}")
  rec={"campaign_id":cid,"variant":variant_id,"quote_net":float(sym.net_pnl.sum()),"positive_symbols":int((sym.net_pnl>0).sum()),"negative_symbols":int((sym.net_pnl<0).sum()),"top_symbol":str(sym.symbol.iloc[0]),"top_symbol_net":float(sym.net_pnl.iloc[0]),"top_symbol_positive_share":float(positive.iloc[0]/positive.sum()),"top5_symbol_positive_share":float(positive.head(5).sum()/positive.sum()),"leave_top_symbol_out_net":float(sym.net_pnl.sum()-sym.net_pnl.iloc[0]),"leave_top5_symbols_out_net":float(sym.net_pnl.sum()-sym.net_pnl.head(5).sum())}; rows.append(rec); sym.insert(0,"campaign_id",cid); details.append(sym)
 p=panels["sp500"]; j=p.symbol_to_col.get("SNDK"); sndk={"present":j is not None}
 if j is not None:
  valid=np.flatnonzero(np.isfinite(p.raw_close[:,j])); members=np.flatnonzero(p.member[:,j]); sndk.update({"price_start":str(p.dates[valid[0]].date()),"price_end":str(p.dates[valid[-1]].date()),"membership_start":str(p.dates[members[0]].date()) if len(members) else None,"membership_end":str(p.dates[members[-1]].date()) if len(members) else None,"rows":len(valid)})
 report={"status":"completed","run_id":"RUN-0026","leaders":rows,"sndk_coverage":sndk,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Exact development quote attribution; concentration blocks standalone printer claims."}; pd.DataFrame(rows).to_csv(OUT/"leader_quote_symbol_concentration.csv",index=False); pd.concat(details).to_csv(OUT/"leader_quote_symbol_detail.csv",index=False); (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); path=CAM/"CAM-0625"/"runs"/"RUN-0026.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="completed"; run["result"]=report; run["decision"]="Reject standalone leaders whose quote PnL is concentrated in a few symbols; preserve only as ensemble inputs if diversified."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":"RUN-0026","event":"completed","leaders":rows,"sndk_coverage":sndk,"holdout_rows_loaded":0})+"\n")
 print(pd.DataFrame(rows).to_string(index=False)); print(json.dumps(sndk,indent=2))
if __name__=="__main__": main()
