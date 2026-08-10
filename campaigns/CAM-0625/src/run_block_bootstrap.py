from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml
from run_ensemble import paths,invvol_weights

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0013"
def simulate(a,rng,n=20000,block=21,length=252):
 starts=rng.integers(0,len(a),size=(n,int(np.ceil(length/block)))); offsets=np.arange(block); idx=(starts[:,:,None]+offsets[None,None,:])%len(a); x=a[idx].reshape(n,-1)[:,:length]; total=x.sum(axis=1); eq=1+np.cumsum(x,axis=1); peak=np.maximum.accumulate(np.column_stack([np.ones(n),eq]),axis=1)[:,1:]; dd=((peak-eq)/peak).max(axis=1); return {"draws":n,"block_length_sessions":block,"path_sessions":length,"probability_positive_return":float((total>0).mean()),"return_p01":float(np.quantile(total,.01)),"return_p05":float(np.quantile(total,.05)),"return_median":float(np.median(total)),"return_p95":float(np.quantile(total,.95)),"maximum_drawdown_median":float(np.median(dd)),"maximum_drawdown_p90":float(np.quantile(dd,.90)),"maximum_drawdown_p95":float(np.quantile(dd,.95)),"maximum_drawdown_p99":float(np.quantile(dd,.99)),"probability_drawdown_above_20pct":float((dd>.20).mean())}
def main():
 OUT.mkdir(parents=True,exist_ok=True); z=paths(False); w=invvol_weights(z); series={"equal":z.mean(axis=1),"causal_inverse_vol":(z*w).sum(axis=1)-w.diff().abs().sum(axis=1).fillna(0)*2/10000}; rng=np.random.default_rng(6252028); result={k:simulate(v.to_numpy(float),rng) for k,v in series.items()}; (OUT/"block_bootstrap.json").write_text(json.dumps(result,indent=2)+"\n"); rows=[{"rule":k,**v} for k,v in result.items()]; pd.DataFrame(rows).to_csv(OUT/"block_bootstrap.csv",index=False); report={"status":"completed","run_id":"RUN-0013","metrics":result,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Descriptive stationary-mixture stress; not a p-value or out-of-sample probability."}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n"); run=CAM/"CAM-0625"/"runs"/"RUN-0013.yaml"; y=yaml.safe_load(run.read_text()); y["status"]="completed"; y["result"]=report; y["decision"]="Use p95/p99 drawdown for sizing caution only; no distributional or independence claim."; run.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a") as f: f.write(json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":"RUN-0013","event":"completed","holdout_rows_loaded":0})+"\n")
 print(pd.DataFrame(rows).to_string(index=False))
if __name__=="__main__": main()
