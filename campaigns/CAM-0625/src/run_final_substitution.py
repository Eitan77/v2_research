from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0022"; IDS=["CAM-0600","CAM-0621","CAM-0624","CAM-0618"]
def dd(s):
 e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max()) if len(s) else 0
def stats(s):
 m=s.groupby(s.index.to_period("M")).sum(); y=s.groupby(s.index.year).sum(); pos=s.clip(lower=0).sort_values(ascending=False); return {"net_simple_return":float(s.sum()),"maximum_drawdown":dd(s),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"monthly_median":float(m.median()),"worst_month":float(m.min()),"top5_positive_day_share":float(pos.head(5).sum()/pos.sum()),"annual_returns":{str(k):float(v) for k,v in y.items()}}
def bootstrap(x):
 rng=np.random.default_rng(62522); n=20000; block=21; path=252; starts=rng.integers(0,len(x)-block+1,size=(n,int(np.ceil(path/block)))); out=np.empty((n,path))
 for j in range(starts.shape[1]):
  width=min(block,path-j*block); out[:,j*block:j*block+width]=x[starts[:,j,None]+np.arange(width)]
 ret=out.sum(1); eq=1+np.cumsum(out,1); peak=np.maximum.accumulate(eq,1); draw=np.max((peak-eq)/peak,1); return {"return_p01":float(np.quantile(ret,.01)),"return_p05":float(np.quantile(ret,.05)),"return_median":float(np.median(ret)),"drawdown_p95":float(np.quantile(draw,.95)),"drawdown_p99":float(np.quantile(draw,.99)),"probability_negative_return":float((ret<0).mean()),"probability_drawdown_over_20pct":float((draw>.2).mean())}
def main():
 OUT.mkdir(parents=True,exist_ok=True); base=pd.read_csv(CAM/"CAM-0600"/"artifacts"/"shared"/"split_repaired_diagnostic_summary.csv").set_index("campaign_id"); repair=pd.read_csv(CAM/"CAM-0600"/"artifacts"/"shared"/"split_repaired_repair_diagnostic_summary.csv").set_index("campaign_id"); full=[]; quote=[]; variants={}
 for cid in IDS:
  row=(repair if cid=="CAM-0621" else base).loc[cid]; v=str(row.selected_variant); variants[cid]=v; run="RUN-0021" if cid=="CAM-0621" else "RUN-0020"; safe=f"{v}__cost_2bps".replace("/","_").replace(":","_"); d=pd.read_parquet(CAM/cid/"artifacts"/run/"variants"/safe/"daily.parquet"); d.date=pd.to_datetime(d.date); full.append(d.set_index("date").net_pnl.rename(cid)); q=pd.read_parquet(CAM/cid/"artifacts"/"RUN-0023"/"daily_0940_2bps_extra.parquet"); q.date=pd.to_datetime(q.date); quote.append(q.set_index("date").net_pnl.rename(cid))
 fs=pd.concat(full,axis=1).fillna(0).mean(1); qs=pd.concat(quote,axis=1).fillna(0).mean(1); folds=[]
 for start,end in (("2020-01-02","2021-12-31"),("2022-01-03","2023-12-29"),("2024-01-02","2026-04-30")):
  x=fs.loc[start:end]; folds.append({"start":start,"end":end,"net_simple_return":float(x.sum()),"maximum_drawdown":dd(x)})
 report={"status":"completed","run_id":"RUN-0022","candidate_sleeves":IDS,"variants":variants,"full":stats(fs),"quote":stats(qs),"folds":folds,"bootstrap":bootstrap(fs.to_numpy()),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Final adapted construction test; no further construction iteration authorized from this result."}; fs.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/"lean3_plus_sector_full_daily.parquet",index=False); qs.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/"lean3_plus_sector_quote_daily.parquet",index=False); (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); path=CAM/"CAM-0625"/"runs"/"RUN-0022.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="completed"; run["result"]=report; run["decision"]="End construction iteration and select only by predefined return-path-tail tradeoff; no promotion."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":"RUN-0022","event":"completed","result":report,"holdout_rows_loaded":0})+"\n")
 print(json.dumps(report,indent=2))
if __name__=="__main__": main()
