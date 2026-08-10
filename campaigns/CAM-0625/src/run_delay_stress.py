from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; SRC=CAM/"CAM-0600"/"src"; sys.path.insert(0,str(SRC))
from deep_strategies import build_deep_variants
from repair_strategies import build_repair_variants
from run_suite import _load_or_build_fundamentals,_preflight
from suite_core import evaluate_weights,load_panels

SELECT={"momentum":("CAM-0600","sp500__mom63_skip0__top3__liquid__panic1","deep"),"multifactor":("CAM-0604","sp500__value_quality__top20__trend0","deep"),"ibs":("CAM-0621","etf__ibs30__top5__hold3__trend1","repair"),"distress":("CAM-0624","qqq__chs_safe__top5__liquid__target8","deep")}

def dd(s):
 e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max())

def main():
 panels=load_panels(); preflight=_preflight(panels); f,cov=_load_or_build_fundamentals(panels); variants={}
 for name,(cid,vid,kind) in SELECT.items():
  vv=build_deep_variants(cid,panels,f) if kind=="deep" else build_repair_variants(cid,panels,f); variants[name]=next(v for v in vv if v.variant_id==vid)
 rows=[]; daily={}; out=CAM/"CAM-0625"/"artifacts"/"RUN-0007"; out.mkdir(parents=True,exist_ok=True)
 for delay in (1,2):
  for cost in (2.,5.,10.):
   sleeve={}
   for name,v in variants.items():
    shifted=np.vstack([np.zeros((delay,v.weights.shape[1])),v.weights[:-delay]])
    met,d,*_=evaluate_weights(v.panel,shifted,cost,holding=v.holding,execution_lag=1,return_override=v.return_override); s=d.net_pnl; sleeve[name]=s; rows.append({"level":"sleeve","name":name,"additional_delay_sessions":delay,"total_execution_lag_sessions":delay+1,"cost_bps_per_side":cost,"net_simple_return":met["net_simple_return"],"maximum_drawdown":met["maximum_drawdown"],"recent12_average_month":met["recent12_average_month"],"recent12_positive_months":met["recent12_positive_months"]})
   z=pd.concat(sleeve,axis=1).fillna(0); z=z.loc[pd.Timestamp("2021-05-03"):pd.Timestamp("2026-04-30")]; s=z.mean(axis=1); m=s.groupby(s.index.to_period("M")).sum(); rows.append({"level":"ensemble","name":"equal_four","additional_delay_sessions":delay,"total_execution_lag_sessions":delay+1,"cost_bps_per_side":cost,"net_simple_return":float(s.sum()),"maximum_drawdown":dd(s),"recent12_average_month":float(m.iloc[-12:].mean()),"recent12_positive_months":int((m.iloc[-12:]>0).sum())}); s.rename("net_pnl").rename_axis("date").reset_index().to_parquet(out/f"daily_equal_delay{delay}_cost{cost:g}.parquet",index=False)
 frame=pd.DataFrame(rows); frame.to_csv(out/"delay_stress_metrics.csv",index=False); report={"status":"completed","run_id":"RUN-0007","metrics":json.loads(frame.to_json(orient="records")),"preflight":preflight,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}; (out/"execution_report.json").write_text(json.dumps(report,indent=2,default=str)+"\n")
 run=CAM/"CAM-0625"/"runs"/"RUN-0007.yaml"; y=yaml.safe_load(run.read_text()); y["status"]="completed"; y["result"]={"rows":len(frame),"ensemble":frame[frame.level=='ensemble'].to_dict('records'),"holdout_rows_loaded":0}; y["decision"]="Use delay decay to distinguish durable monthly ranking from short-lived IBS contribution; no promotion."; run.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a") as fobj: fobj.write(json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":"RUN-0007","event":"completed","holdout_rows_loaded":0})+"\n")
 print(frame.to_string(index=False))
if __name__=="__main__": main()
