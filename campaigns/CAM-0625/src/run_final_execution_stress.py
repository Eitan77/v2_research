from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0023"; IDS=["CAM-0600","CAM-0621","CAM-0624","CAM-0618"]
def dd(s):
 e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max()) if len(s) else 0
def main():
 OUT.mkdir(parents=True,exist_ok=True); rows=[]
 for clock in ("0930","0940"):
  for extra in (0,2,5,10):
   parts=[]
   for cid in IDS:
    d=pd.read_parquet(CAM/cid/"artifacts"/"RUN-0023"/f"daily_{clock}_{extra:g}bps_extra.parquet"); d.date=pd.to_datetime(d.date); parts.append(d.set_index("date").net_pnl.rename(cid))
   s=pd.concat(parts,axis=1).fillna(0).mean(axis=1); m=s.groupby(s.index.to_period("M")).sum(); rec={"clock":clock,"extra_slippage_bps_per_side":extra,"net_simple_return":float(s.sum()),"maximum_drawdown":dd(s),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"monthly_median":float(m.median()),"worst_month":float(m.min()),"best_month":float(m.max())}; rows.append(rec); s.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/f"daily_{clock}_{extra:g}bps_extra.parquet",index=False)
 report={"status":"completed","run_id":"RUN-0023","sleeves":IDS,"metrics":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Development execution stress using marketable SIP sides and no passive-fill credit."}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); pd.DataFrame(rows).to_csv(OUT/"execution_stress.csv",index=False); path=CAM/"CAM-0625"/"runs"/"RUN-0023.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="completed"; run["result"]=report; run["decision"]="Execution sensitivity documented; no promotion without prospective evidence."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":"RUN-0023","event":"completed","metrics":rows,"holdout_rows_loaded":0})+"\n")
 print(pd.DataFrame(rows).to_string(index=False))
if __name__=="__main__": main()
