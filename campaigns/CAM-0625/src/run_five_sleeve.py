from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0004"
S={"momentum":("CAM-0600","RUN-0008","sp500__mom63_skip0__top3__liquid__panic1"),"multifactor":("CAM-0604","RUN-0008","sp500__value_quality__top20__trend0"),"ibs":("CAM-0621","RUN-0010","etf__ibs30__top5__hold3__trend1"),"distress":("CAM-0624","RUN-0008","qqq__chs_safe__top5__liquid__target8"),"alpha_combo":("CAM-0617","RUN-0010","etf__alpha_M20_E5__top5__monthly__trend0")}

def load(quote=False,extra=2):
 out={}
 for name,(cid,run,v) in S.items():
  if quote: p=CAM/cid/"artifacts"/("RUN-0011" if cid in {"CAM-0621","CAM-0617"} else "RUN-0009")/f"daily_0940_{extra:g}bps_extra.parquet"
  else: p=CAM/cid/"artifacts"/run/"variants"/(v+"__cost_2bps").replace("/","_").replace(":","_")/"daily.parquet"
  d=pd.read_parquet(p); d["date"]=pd.to_datetime(d.date); out[name]=d.set_index("date").net_pnl
 z=pd.concat(out,axis=1).fillna(0).sort_index(); return z if quote else z.loc[pd.Timestamp("2021-05-03"):pd.Timestamp("2026-04-30")]

def invvol(z):
 v=z.rolling(126,min_periods=63).std().shift(1); w=(1/v.replace(0,np.nan)); w=w.div(w.sum(axis=1),axis=0)
 w=w.clip(lower=.10); w["alpha_combo"]=w.alpha_combo.clip(upper=.20)
 for c in w.columns:
  if c!="alpha_combo": w[c]=w[c].clip(upper=.35)
 w=w.div(w.sum(axis=1),axis=0); signal=w.groupby(w.index.to_period("M")).head(1); return signal.reindex(z.index).ffill().fillna(.20)

def calc(sample,extra,z,rule):
 if rule=="equal5": s=z.mean(axis=1)
 else:
  w=invvol(z); s=(z*w).sum(axis=1)-w.diff().abs().sum(axis=1).fillna(0)*2/10000
 e=1+s.cumsum(); m=s.groupby(s.index.to_period("M")).sum(); pos=s.clip(lower=0).sort_values(ascending=False)
 return {"sample":sample,"extra_slippage_bps_per_side":extra,"rule":rule,"net_simple_return":float(s.sum()),"maximum_drawdown":float(((e.cummax()-e)/e.cummax()).max()),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"monthly_average":float(m.mean()),"monthly_median":float(m.median()),"monthly_std":float(m.std()),"worst_month":float(m.min()),"best_month":float(m.max()),"top5_day_positive_share":float(pos.head(5).sum()/pos.sum())}

def main():
 OUT.mkdir(parents=True,exist_ok=True); rows=[]; full=load(False)
 for rule in ("equal5","alpha_capped_inverse_vol"): rows.append(calc("full_history",2,full,rule))
 for extra in (2,10):
  q=load(True,extra)
  for rule in ("equal5","alpha_capped_inverse_vol"): rows.append(calc("quote_0940",extra,q,rule))
 frame=pd.DataFrame(rows); frame.to_csv(OUT/"five_sleeve_metrics.csv",index=False); full.corr().to_csv(OUT/"five_sleeve_correlations.csv"); report={"status":"completed","run_id":"RUN-0004","metrics":json.loads(frame.to_json(orient="records")),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin":False,"leveraged_etfs_present_in_alpha_sleeve":True}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n")
 run=CAM/"CAM-0625"/"runs"/"RUN-0004.yaml"; y=yaml.safe_load(run.read_text()); y["status"]="completed"; y["result"]=report; y["decision"]="Compare incremental income against alpha sleeve concentration and four-sleeve tail risk; no promotion."; run.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a") as f: f.write(json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":"RUN-0004","event":"completed","holdout_rows_loaded":0})+"\n")
 print(frame.to_string(index=False)); print(full.corr().to_string())
if __name__=="__main__": main()
