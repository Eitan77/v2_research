from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0018"; SPECS={"CAM-0600":"RUN-0020","CAM-0604":"RUN-0020","CAM-0621":"RUN-0021","CAM-0624":"RUN-0020"}
def dd(s):
 e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max()) if len(s) else 0
def main():
 OUT.mkdir(parents=True,exist_ok=True); rows=[]; selected=[]; blockers=[]
 for cid,run in SPECS.items():
  local=[]
  for p in (CAM/cid/"artifacts"/run/"variants").glob("*/daily.parquet"):
   d=pd.read_parquet(p); d.date=pd.to_datetime(d.date); s=d.set_index("date").net_pnl.sort_index(); train=s.loc[:pd.Timestamp("2023-12-29")]; val=s.loc[pd.Timestamp("2024-01-02"):pd.Timestamp("2026-04-30")]; m=train.groupby(train.index.to_period("M")).sum(); active=int((train.abs()>1e-12).sum()); rec={"campaign_id":cid,"variant_dir":p.parent.name,"training_net":float(train.sum()),"training_drawdown":dd(train),"training_positive_month_fraction":float((m>0).mean()) if len(m) else 0,"training_active_days":active,"validation_net":float(val.sum()),"validation_drawdown":dd(val)}; rec["eligible"]=rec["training_drawdown"]<=.20 and rec["training_positive_month_fraction"]>=.60 and active>=252; local.append(rec); rows.append(rec)
  good=[x for x in local if x["eligible"]]
  if good: selected.append(max(good,key=lambda x:x["training_net"]))
  else: blockers.append({"campaign_id":cid,"reason":"zero candidates met frozen training quality gate","candidate_count":len(local),"best_training_positive_month_fraction":max(x["training_positive_month_fraction"] for x in local),"minimum_training_drawdown":min(x["training_drawdown"] for x in local)})
 pd.DataFrame(rows).to_csv(OUT/"candidate_training_validation.csv",index=False); pd.DataFrame(selected).to_csv(OUT/"selected_components.csv",index=False); report={"status":"completed_no_candidate" if blockers else "completed","run_id":"RUN-0018","selected_components":selected,"blockers":blockers,"ensemble_validation":None,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Retrospective pseudo-OOS only; fail closed when any family is unavailable."}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
 path=CAM/"CAM-0625"/"runs"/"RUN-0018.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]=report["status"]; run["result"]=report; run["decision"]="Do not call the repaired ensemble historically identifiable if any family fails the frozen gate."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":"RUN-0018","event":report["status"],"blockers":blockers,"holdout_rows_loaded":0})+"\n")
 print(json.dumps(report,indent=2))
if __name__=="__main__": main()
