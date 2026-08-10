from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; SH=CAM/"CAM-0600"/"artifacts"/"shared"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0008"
IDS={"CAM-0600","CAM-0604","CAM-0621","CAM-0624"}
def main():
 OUT.mkdir(parents=True,exist_ok=True); x=pd.read_parquet(SH/"target_change_replay_0940.parquet"); x=x[x.campaign_id.isin(IDS)&x.effective_complete].copy(); buy=x.side.eq("buy"); x["display_price"]=np.where(buy,x.ask_price,x.bid_price); x["display_shares"]=np.where(buy,x.ask_size,x.bid_size); x["display_notional"]=x.display_price*x.display_shares; x["portfolio_delta_weight"]=.25*x.delta_weight.abs()
 rows=[]
 for part in (.01,.05,.10):
  cap=part*x.display_notional/x.portfolio_delta_weight.replace(0,np.nan)
  for label,g in [("all",cap),*[(cid,cap[x.campaign_id.eq(cid)]) for cid in sorted(IDS)]]:
   rows.append({"participation_of_displayed_size":part,"scope":label,"roles":int(g.notna().sum()),"minimum_capital_dollars":float(g.min()),"p01_capital_dollars":float(g.quantile(.01)),"p05_capital_dollars":float(g.quantile(.05)),"p10_capital_dollars":float(g.quantile(.10)),"median_capital_dollars":float(g.median())})
 frame=pd.DataFrame(rows); frame.to_csv(OUT/"displayed_nbbo_capacity.csv",index=False); x[["campaign_id","session_date","symbol","side","delta_weight","display_price","display_shares","display_notional","portfolio_delta_weight"]].to_parquet(OUT/"capacity_roles.parquet",index=False)
 report={"status":"completed","run_id":"RUN-0008","roles":len(x),"interpretation":"Snapshot top-of-book liquidity floor only; not a capacity claim. Size can replenish or disappear and market impact is not modeled.","metrics":json.loads(frame.to_json(orient="records")),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n")
 run=CAM/"CAM-0625"/"runs"/"RUN-0008.yaml"; y=yaml.safe_load(run.read_text()); y["status"]="completed"; y["result"]=report; y["decision"]="Use the lower displayed-size percentiles as a conservative sizing warning only; require order-book and impact replay before deployable-capacity claims."; run.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a") as f: f.write(json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":"RUN-0008","event":"completed","roles":len(x),"holdout_rows_loaded":0})+"\n")
 print(frame.to_string(index=False))
if __name__=="__main__": main()
