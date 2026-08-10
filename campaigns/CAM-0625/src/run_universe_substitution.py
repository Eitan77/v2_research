from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0012"
FIX={"ibs":("CAM-0621","RUN-0010","etf__ibs30__top5__hold3__trend1"),"distress":("CAM-0624","RUN-0008","qqq__chs_safe__top5__liquid__target8")}
VAR={"momentum_sp500":("CAM-0600","sp500__mom63_skip0__top3__liquid__panic1"),"momentum_qqq":("CAM-0600","qqq__mom63_skip0__top3__liquid__panic1"),"multifactor_sp500":("CAM-0604","sp500__value_quality__top20__trend0"),"multifactor_qqq":("CAM-0604","qqq__value_quality__top20__trend0")}
def load(cid,run,v):
 p=CAM/cid/"artifacts"/run/"variants"/(v+"__cost_2bps").replace("/","_").replace(":","_")/"daily.parquet"; d=pd.read_parquet(p); d["date"]=pd.to_datetime(d.date); return d.set_index("date").net_pnl
def main():
 OUT.mkdir(parents=True,exist_ok=True); fixed={k:load(*v) for k,v in FIX.items()}; alt={k:load(cid,"RUN-0008",v) for k,(cid,v) in VAR.items()}; rows=[]
 for mu in ("sp500","qqq"):
  for fu in ("sp500","qqq"):
   z=pd.concat({"momentum":alt[f"momentum_{mu}"],"multifactor":alt[f"multifactor_{fu}"],**fixed},axis=1).fillna(0).sort_index().loc[pd.Timestamp("2021-05-03"):pd.Timestamp("2026-04-30")]; s=z.mean(axis=1); e=1+s.cumsum(); m=s.groupby(s.index.to_period("M")).sum(); recent=m.iloc[-12:]; name=f"momentum_{mu}__multifactor_{fu}"; rows.append({"variant_id":name,"net_simple_return":float(s.sum()),"maximum_drawdown":float(((e.cummax()-e)/e.cummax()).max()),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"recent12_net":float(recent.sum()),"recent12_average_month":float(recent.mean()),"recent12_positive_months":int((recent>0).sum()),"worst_month":float(m.min()),"best_month":float(m.max())}); s.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/f"daily_{name}.parquet",index=False)
 frame=pd.DataFrame(rows); frame.to_csv(OUT/"universe_substitution.csv",index=False); report={"status":"completed","run_id":"RUN-0012","metrics":json.loads(frame.to_json(orient='records')),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"quote_replay":"not_run_for_substitutions"}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n"); run=CAM/"CAM-0625"/"runs"/"RUN-0012.yaml"; y=yaml.safe_load(run.read_text()); y["status"]="completed"; y["result"]=report; y["decision"]="Use substitution evidence to assess reconstruction dependence; do not replace the quote-validated core without separate target-change replay."; run.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a") as f: f.write(json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":"RUN-0012","event":"completed","holdout_rows_loaded":0})+"\n")
 print(frame.to_string(index=False))
if __name__=="__main__": main()
