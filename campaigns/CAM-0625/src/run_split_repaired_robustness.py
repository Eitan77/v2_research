from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0021"; NAMES=("parent4","leave_out_CAM-0604","parent_plus_sector"); FOLDS=(("2020-01-02","2021-12-31"),("2022-01-03","2023-12-29"),("2024-01-02","2026-04-30"))
def dd(x):
 e=1+np.cumsum(x); peak=np.maximum.accumulate(e); return float(np.max((peak-e)/peak)) if len(x) else 0
def bootstrap(x,rng,n=20000,block=21,path=252):
 starts=rng.integers(0,max(1,len(x)-block+1),size=(n,int(np.ceil(path/block)))); out=np.empty((n,path));
 for j in range(starts.shape[1]):
  cols=np.arange(block); vals=x[np.minimum(starts[:,j,None]+cols[None,:],len(x)-1)]; lo=j*block; out[:,lo:min(path,lo+block)]=vals[:,:min(block,path-lo)]
 ret=out.sum(axis=1); equity=1+np.cumsum(out,axis=1); peak=np.maximum.accumulate(equity,axis=1); draw=np.max((peak-equity)/peak,axis=1); return {"return_p01":float(np.quantile(ret,.01)),"return_p05":float(np.quantile(ret,.05)),"return_median":float(np.median(ret)),"drawdown_p95":float(np.quantile(draw,.95)),"drawdown_p99":float(np.quantile(draw,.99)),"probability_negative_return":float((ret<0).mean()),"probability_drawdown_over_20pct":float((draw>.20).mean())}
def main():
 OUT.mkdir(parents=True,exist_ok=True); rng=np.random.default_rng(62521); results={}
 for name in NAMES:
  p=CAM/"CAM-0625"/"artifacts"/"RUN-0020"/f"{name}_full_daily.parquet"; d=pd.read_parquet(p); d.date=pd.to_datetime(d.date); s=d.set_index("date").net_pnl.sort_index(); folds=[]
  for start,end in FOLDS:
   x=s.loc[start:end]; m=x.groupby(x.index.to_period("M")).sum(); folds.append({"start":start,"end":end,"net_simple_return":float(x.sum()),"maximum_drawdown":dd(x.to_numpy()),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum())})
  results[name]={"folds":folds,"bootstrap":bootstrap(s.to_numpy(),rng)}
 report={"status":"completed","run_id":"RUN-0021","results":results,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Adapted development robustness, not genuine OOS."}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); path=CAM/"CAM-0625"/"runs"/"RUN-0021.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="completed"; run["result"]=report; run["decision"]="Choose only if fold stability and tail risk support the simpler construction; no promotion."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":"RUN-0021","event":"completed","results":results,"holdout_rows_loaded":0})+"\n")
 print(json.dumps(report,indent=2))
if __name__=="__main__": main()
