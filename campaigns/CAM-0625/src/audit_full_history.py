from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml
from run_ensemble import paths,invvol_weights

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0005"

def drawdown(s):
 e=1+s.cumsum(); peak=e.cummax(); underwater=e<peak-1e-12; groups=(underwater!=underwater.shift(fill_value=False)).cumsum(); durations=underwater.groupby(groups).sum(); return float(((peak-e)/peak).max()),int(durations.max() if len(durations) else 0)

def stats(s):
 m=s.groupby(s.index.to_period("M")).sum(); dd,dur=drawdown(s); out={"net_simple_return":float(s.sum()),"maximum_drawdown":dd,"longest_underwater_trading_days":dur,"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"monthly_average":float(m.mean()),"monthly_median":float(m.median()),"monthly_std":float(m.std())}
 for n in (12,18,24):
  r=m.rolling(n,min_periods=n).sum().dropna(); out[f"rolling{n}_worst"]=float(r.min()); out[f"rolling{n}_median"]=float(r.median()); out[f"rolling{n}_positive_fraction"]=float((r>0).mean()); out[f"recent{n}"]=float(m.iloc[-n:].sum()); out[f"recent{n}_percentile"]=float((r<=m.iloc[-n:].sum()).mean())
 return out

def make(z,rule):
 if rule=="equal": return z.mean(axis=1)
 w=invvol_weights(z); return (z*w).sum(axis=1)-w.diff().abs().sum(axis=1).fillna(0)*2/10000

def main():
 OUT.mkdir(parents=True,exist_ok=True); z=paths(False); rows=[]; years=[]; folds=[]; rng=np.random.default_rng(6252027); boot={}
 for omitted in (None,*z.columns):
  sub=z if omitted is None else z.drop(columns=omitted); label="all" if omitted is None else f"without_{omitted}"
  for rule in ("equal","causal_inverse_vol"):
   s=make(sub,rule); rows.append({"rule":rule,"sleeves":label,**stats(s)})
   for year,v in s.groupby(s.index.year).sum().items(): years.append({"rule":rule,"sleeves":label,"year":int(year),"net_pnl":float(v)})
   chunks=np.array_split(np.arange(len(s)),3)
   for k,idx in enumerate(chunks,1): folds.append({"rule":rule,"sleeves":label,"fold":k,"start":str(s.index[idx[0]].date()),"end":str(s.index[idx[-1]].date()),"net_pnl":float(s.iloc[idx].sum()),"maximum_drawdown":drawdown(s.iloc[idx])[0]})
   if label=="all":
    m=s.groupby(s.index.to_period("M")).sum().to_numpy(); sims=m[rng.integers(0,len(m),size=(20000,12))].sum(axis=1); boot[rule]={"draws":20000,"source_months":len(m),"probability_positive_12_month_sum":float((sims>0).mean()),"p05":float(np.quantile(sims,.05)),"median":float(np.median(sims)),"p95":float(np.quantile(sims,.95))}
 pd.DataFrame(rows).to_csv(OUT/"full_history_robustness.csv",index=False); pd.DataFrame(years).to_csv(OUT/"yearly.csv",index=False); pd.DataFrame(folds).to_csv(OUT/"chronological_folds.csv",index=False); (OUT/"monthly_bootstrap.json").write_text(json.dumps(boot,indent=2)+"\n")
 report={"status":"completed","run_id":"RUN-0005","rows":len(rows),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"bootstrap":boot}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n")
 run=CAM/"CAM-0625"/"runs"/"RUN-0005.yaml"; y=yaml.safe_load(run.read_text()); y["status"]="completed"; y["result"]=report; y["decision"]="Use rolling and fold evidence to distinguish durable ensemble quality from exceptional recent regime strength; no promotion."; run.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a") as f: f.write(json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":"RUN-0005","event":"completed","holdout_rows_loaded":0})+"\n")
 print(pd.DataFrame(rows).to_string(index=False)); print('\nFOLDS\n',pd.DataFrame(folds)[pd.DataFrame(folds).sleeves=='all'].to_string(index=False)); print(json.dumps(boot,indent=2))
if __name__=="__main__": main()
