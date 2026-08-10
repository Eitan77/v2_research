from __future__ import annotations
import json
import pandas as pd
import yaml
from suite_core import CAMPAIGNS

SH=CAMPAIGNS/"CAM-0600"/"artifacts"/"shared"; REPAIR={"CAM-0608","CAM-0609","CAM-0617","CAM-0621"}
def main():
 m=pd.read_csv(SH/"target_change_quote_metrics.csv"); q=m[(m.clock.astype(str).str.zfill(4)=="0940")&(m.extra_slippage_bps_per_side==2)]
 for r in q.itertuples(index=False):
  run="RUN-0011" if r.campaign_id in REPAIR else "RUN-0009"; p=CAMPAIGNS/r.campaign_id/"runs"/f"{run}.yaml"; y=yaml.safe_load(p.read_text()); y["status"]="completed"; y["result"]={"net_simple_return":float(r.net_simple_return),"maximum_drawdown":float(r.maximum_drawdown),"positive_months":int(r.positive_months),"negative_months":int(r.negative_months),"active_sessions":int(r.active_sessions),"role_coverage":float(r.role_coverage),"clock":"09:40","additional_slippage_bps_per_side":2,"reference_price":"09:30 SIP midpoint","holdout_rows_loaded":0}; y["decision"]="Corrected target-change execution evidence; development-only and not promotion evidence."; p.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
  with (CAMPAIGNS/r.campaign_id/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":run,"event":"completed","net_simple_return":float(r.net_simple_return),"clock":"09:40","extra_bps":2,"holdout_rows_loaded":0})+"\n")
 print(f"finalized {len(q)} quote run records")
if __name__=="__main__": main()
