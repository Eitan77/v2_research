from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; SH=CAM/"CAM-0600"/"artifacts"/"shared"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0025"
def dd(s):
 e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max()) if len(s) else 0
def main():
 OUT.mkdir(parents=True,exist_ok=True); q=pd.read_csv(SH/"split_repaired_quote_metrics_RUN-0023.csv"); central=q[(q.clock.astype(str).str.zfill(4)=="0940")&(q.extra_slippage_bps_per_side==2)].sort_values("net_simple_return",ascending=False); leaders=central.head(5).campaign_id.tolist(); expected=["CAM-0612","CAM-0611","CAM-0610","CAM-0623","CAM-0600"]
 if leaders!=expected: raise RuntimeError(f"leader reconciliation failed: {leaders}")
 sel=pd.read_csv(SH/"split_repaired_diagnostic_summary.csv").set_index("campaign_id"); series={}; rows=[]; early=[]
 for cid in leaders:
  variant=str(sel.loc[cid,"selected_variant"]); safe=f"{variant}__cost_2bps".replace("/","_").replace(":","_"); root=CAM/cid/"artifacts"/"RUN-0020"/"variants"/safe; d=pd.read_parquet(root/"daily.parquet"); d.date=pd.to_datetime(d.date); s=d.set_index("date").net_pnl.sort_index(); series[cid]=s; symbols=pd.read_csv(root/"symbols.csv").sort_values("net_pnl",ascending=False); positive=symbols.net_pnl.clip(lower=0); month=s.groupby(s.index.to_period("M")).sum(); q2=central[central.campaign_id==cid].iloc[0]; q10=q[(q.campaign_id==cid)&(q.clock.astype(str).str.zfill(4)=="0940")&(q.extra_slippage_bps_per_side==10)].iloc[0]; rows.append({"campaign_id":cid,"variant":variant,"full_net":float(s.sum()),"full_drawdown":dd(s),"full_positive_months":int((month>0).sum()),"full_negative_months":int((month<0).sum()),"top_symbol":str(symbols.symbol.iloc[0]),"top_symbol_pnl":float(symbols.net_pnl.iloc[0]),"top5_symbol_positive_share":float(positive.head(5).sum()/positive.sum()) if positive.sum()>0 else None,"quote_2bps_net":float(q2.net_simple_return),"quote_2bps_drawdown":float(q2.maximum_drawdown),"quote_2bps_months":f"{int(q2.positive_months)}/{int(q2.negative_months)}","quote_10bps_net":float(q10.net_simple_return),"quote_10bps_drawdown":float(q10.maximum_drawdown)})
  candidates=[]
  for p in (CAM/cid/"artifacts"/"RUN-0020"/"variants").glob("*/daily.parquet"):
   x=pd.read_parquet(p); x.date=pd.to_datetime(x.date); z=x.set_index("date").net_pnl.sort_index().loc[:"2023-12-29"]; m=z.groupby(z.index.to_period("M")).sum(); active=int((z.abs()>1e-12).sum()); rec={"variant":p.parent.name,"net":float(z.sum()),"drawdown":dd(z),"positive_month_fraction":float((m>0).mean()),"active_days":active}; rec["eligible"]=rec["drawdown"]<=.2 and rec["positive_month_fraction"]>=.6 and active>=252; candidates.append(rec)
  good=[x for x in candidates if x["eligible"]]; early.append({"campaign_id":cid,"eligible_variants":len(good),"selected_pre2024":max(good,key=lambda x:x["net"]) if good else None,"best_positive_month_fraction":max(x["positive_month_fraction"] for x in candidates),"minimum_drawdown":min(x["drawdown"] for x in candidates)})
 frame=pd.concat(series,axis=1).fillna(0); report={"status":"completed","run_id":"RUN-0025","leaders":rows,"full_daily_correlations":frame.corr().to_dict(),"early_gate":early,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Descriptive audit of retrospectively selected recent leaders; not promotion evidence."}; pd.DataFrame(rows).to_csv(OUT/"leader_summary.csv",index=False); frame.corr().to_csv(OUT/"leader_correlations.csv"); (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); path=CAM/"CAM-0625"/"runs"/"RUN-0025.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="completed"; run["result"]=report; run["decision"]="Treat high-return leaders as distinct only if overlap, concentration, and early gate support that claim."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":"RUN-0025","event":"completed","leaders":rows,"early_gate":early,"holdout_rows_loaded":0})+"\n")
 print(pd.DataFrame(rows).to_string(index=False)); print(frame.corr().round(3).to_string()); print(json.dumps(early,indent=2))
if __name__=="__main__": main()
