from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0017"; IDS=("CAM-0600","CAM-0604","CAM-0621","CAM-0624"); REPAIR={"CAM-0621"}

def dd(s):
 e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max()) if len(s) else 0.0

def metrics(s):
 m=s.groupby(s.index.to_period("M")).sum(); y=s.groupby(s.index.year).sum(); return {"net_simple_return":float(s.sum()),"maximum_drawdown":dd(s),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"monthly_average":float(m.mean()),"monthly_median":float(m.median()),"worst_month":float(m.min()),"best_month":float(m.max()),"annual_returns":{str(k):float(v) for k,v in y.items()}}

def main():
 OUT.mkdir(parents=True,exist_ok=True); base=pd.read_csv(CAM/"CAM-0600"/"artifacts"/"shared"/"split_repaired_diagnostic_summary.csv").set_index("campaign_id"); repair=pd.read_csv(CAM/"CAM-0600"/"artifacts"/"shared"/"split_repaired_repair_diagnostic_summary.csv").set_index("campaign_id"); full={}; quote={}; variants={}
 for cid in IDS:
  row=(repair if cid in REPAIR else base).loc[cid]; variant=str(row.selected_variant); variants[cid]=variant; parent="RUN-0021" if cid in REPAIR else "RUN-0020"; safe=f"{variant}__cost_2bps".replace("/","_").replace(":","_"); d=pd.read_parquet(CAM/cid/"artifacts"/parent/"variants"/safe/"daily.parquet"); d.date=pd.to_datetime(d.date); full[cid]=d.set_index("date").net_pnl.sort_index(); q=pd.read_parquet(CAM/cid/"artifacts"/"RUN-0023"/"daily_0940_2bps_extra.parquet"); q.date=pd.to_datetime(q.date); quote[cid]=q.set_index("date").net_pnl.sort_index()
 frame=pd.concat(full,axis=1).fillna(0); equal=frame.mean(axis=1); vol=frame.rolling(63,min_periods=42).std(ddof=1).shift(1); inv=1/vol.replace(0,np.nan); weights=inv.div(inv.sum(axis=1),axis=0).fillna(0); inverse=(frame*weights).sum(axis=1)
 qframe=pd.concat(quote,axis=1).fillna(0); qweights=weights.reindex(qframe.index).ffill().fillna(0); qequal=qframe.mean(axis=1); qinverse=(qframe*qweights).sum(axis=1)
 series={"full_equal":equal,"full_inverse_vol":inverse,"quote_equal":qequal,"quote_inverse_vol":qinverse}; report={"status":"completed","run_id":"RUN-0017","variants":variants,"metrics":{name:metrics(s) for name,s in series.items()},"component_correlations":frame.corr().to_dict(),"quote_component_metrics":{cid:metrics(s) for cid,s in quote.items()},"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Adapted development evidence after data repair; not genuine OOS."}
 for name,s in series.items(): s.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/f"{name}_daily.parquet",index=False)
 weights.rename_axis("date").reset_index().to_parquet(OUT/"causal_inverse_vol_weights.parquet",index=False); (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
 path=CAM/"CAM-0625"/"runs"/"RUN-0017.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="completed"; run["result"]=report; run["decision"]="Proceed only to repaired stability, concentration, and pseudo-OOS diagnostics; no promotion."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":"RUN-0017","event":"completed","metrics":report["metrics"],"holdout_rows_loaded":0})+"\n")
 print(json.dumps(report,indent=2))

if __name__=="__main__": main()
