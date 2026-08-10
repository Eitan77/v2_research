from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0014"; SPECS={"CAM-0600":"RUN-0008","CAM-0604":"RUN-0008","CAM-0621":"RUN-0010","CAM-0624":"RUN-0008"}
def dd(s):
 e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max()) if len(s) else 0
def main():
 OUT.mkdir(parents=True,exist_ok=True); choices=[]; validation={}; all_candidates=[]
 for cid,run in SPECS.items():
  root=CAM/cid/"artifacts"/run/"variants"; candidates=[]
  for p in root.glob("*/daily.parquet"):
   d=pd.read_parquet(p); d["date"]=pd.to_datetime(d.date); s=d.set_index("date").net_pnl.sort_index(); train=s.loc[:pd.Timestamp("2023-12-29")]; val=s.loc[pd.Timestamp("2024-01-02"):pd.Timestamp("2026-04-30")]; m=train.groupby(train.index.to_period("M")).sum(); active=int((train.abs()>1e-12).sum()); rec={"campaign_id":cid,"variant_dir":p.parent.name,"training_net":float(train.sum()),"training_drawdown":dd(train),"training_positive_month_fraction":float((m>0).mean()) if len(m) else 0,"training_active_days":active,"validation_net":float(val.sum()),"validation_drawdown":dd(val),"validation_positive_months":int((val.groupby(val.index.to_period('M')).sum()>0).sum()),"validation_negative_months":int((val.groupby(val.index.to_period('M')).sum()<0).sum())}; rec["eligible"]=rec["training_drawdown"]<=.20 and rec["training_positive_month_fraction"]>=.60 and active>=252; candidates.append((rec,s)); all_candidates.append(rec)
  eligible=[x for x in candidates if x[0]["eligible"]]
  if not eligible: raise RuntimeError(f"no eligible early selector candidate {cid}")
  chosen=max(eligible,key=lambda x:x[0]["training_net"]); choices.append(chosen[0]); validation[cid]=chosen[1].loc[pd.Timestamp("2024-01-02"):pd.Timestamp("2026-04-30")]
 z=pd.concat(validation,axis=1).fillna(0); ensemble=z.mean(axis=1); monthly=ensemble.groupby(ensemble.index.to_period("M")).sum(); ensemble_metrics={"validation_start":str(ensemble.index.min().date()),"validation_end":str(ensemble.index.max().date()),"net_simple_return":float(ensemble.sum()),"maximum_drawdown":dd(ensemble),"positive_months":int((monthly>0).sum()),"negative_months":int((monthly<0).sum()),"monthly_average":float(monthly.mean()),"monthly_median":float(monthly.median()),"worst_month":float(monthly.min()),"best_month":float(monthly.max())}; pd.DataFrame(all_candidates).to_csv(OUT/"candidate_training_validation.csv",index=False); pd.DataFrame(choices).to_csv(OUT/"selected_components.csv",index=False); ensemble.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/"daily_equal_selected_through_2023.parquet",index=False); monthly.rename("net_pnl").rename_axis("month").reset_index().to_csv(OUT/"monthly_equal_selected_through_2023.csv",index=False); report={"status":"completed","run_id":"RUN-0014","selected_components":choices,"ensemble_validation":ensemble_metrics,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Retrospective pseudo-OOS only; grid and research process were not historically frozen."}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n"); run=CAM/"CAM-0625"/"runs"/"RUN-0014.yaml"; y=yaml.safe_load(run.read_text()); y["status"]="completed"; y["result"]=report; y["decision"]="Compare early-selected ensemble with full-selected core; no genuine OOS or promotion claim."; run.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a") as f: f.write(json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":"RUN-0014","event":"completed","holdout_rows_loaded":0})+"\n")
 print(pd.DataFrame(choices).to_string(index=False)); print(json.dumps(ensemble_metrics,indent=2))
if __name__=="__main__": main()
