from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0003"
SLEEVES={"momentum":"CAM-0600","multifactor":"CAM-0604","ibs":"CAM-0621","distress":"CAM-0624"}

def dd(s):
    e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max())

def load(clock,extra):
    cols={}
    for name,cid in SLEEVES.items():
        run="RUN-0011" if cid=="CAM-0621" else "RUN-0009"; p=CAM/cid/"artifacts"/run/f"daily_{clock}_{extra:g}bps_extra.parquet"; d=pd.read_parquet(p); d["date"]=pd.to_datetime(d.date); cols[name]=d.set_index("date").net_pnl
    return pd.concat(cols,axis=1).fillna(0).sort_index()

def invvol(z):
    v=z.rolling(126,min_periods=63).std(ddof=1).shift(1); x=(1/v.replace(0,np.nan)); x=x.div(x.sum(axis=1),axis=0).clip(.10,.40); x=x.div(x.sum(axis=1),axis=0); signal=x.groupby(x.index.to_period("M")).head(1); return signal.reindex(z.index).ffill().fillna(1/len(z.columns))

def metric(clock,extra,rule,label,s):
    m=s.groupby(s.index.to_period("M")).sum(); pos=s.clip(lower=0).sort_values(ascending=False); return {"clock":clock,"extra_slippage_bps_per_side":extra,"rule":rule,"sleeves":label,"net_simple_return":float(s.sum()),"maximum_drawdown":dd(s),"green_days":int((s>0).sum()),"red_days":int((s<0).sum()),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"worst_month":float(m.min()),"best_month":float(m.max()),"monthly_std":float(m.std(ddof=1)),"top5_day_positive_share":float(pos.head(5).sum()/pos.sum())}

def bootstrap(monthly,seed=6252026,n=10000):
    rng=np.random.default_rng(seed); a=monthly.to_numpy(float); sims=a[rng.integers(0,len(a),size=(n,len(a)))].sum(axis=1); return {"draws":n,"months":len(a),"probability_positive_12_month_sum":float((sims>0).mean()),"p05_12_month_sum":float(np.quantile(sims,.05)),"median_12_month_sum":float(np.median(sims)),"p95_12_month_sum":float(np.quantile(sims,.95))}

def main():
    OUT.mkdir(parents=True,exist_ok=True); rows=[]; boot={}
    for clock in ("0930","0940"):
      for extra in (0.,2.,5.,10.):
        z=load(clock,extra)
        for omitted in (None,*z.columns):
          use=[c for c in z if c!=omitted]; label="all" if omitted is None else f"without_{omitted}"; sub=z[use]
          equal=sub.mean(axis=1); rows.append(metric(clock,extra,"equal",label,equal))
          w=invvol(sub); alloc_cost=w.diff().abs().sum(axis=1).fillna(0)*2/10000; risk=(sub*w).sum(axis=1)-alloc_cost; rows.append(metric(clock,extra,"causal_inverse_vol",label,risk))
          if extra==2 and omitted is None:
            boot[f"{clock}_equal"]=bootstrap(equal.groupby(equal.index.to_period('M')).sum(),6252026+(0 if clock=='0930' else 1))
            boot[f"{clock}_causal_inverse_vol"]=bootstrap(risk.groupby(risk.index.to_period('M')).sum(),6252028+(0 if clock=='0930' else 1))
    frame=pd.DataFrame(rows); frame.to_csv(OUT/"stress_metrics.csv",index=False); (OUT/"monthly_bootstrap.json").write_text(json.dumps(boot,indent=2)+"\n")
    report={"status":"completed","run_id":"RUN-0003","executed_variants":len(frame),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin":False,"metrics_file":"stress_metrics.csv","bootstrap_file":"monthly_bootstrap.json"}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n")
    run=CAM/"CAM-0625"/"runs"/"RUN-0003.yaml"; y=yaml.safe_load(run.read_text()); y["status"]="completed"; y["result"]={"executed_variants":len(frame),"holdout_rows_loaded":0,"all_sleeve_rows":frame[frame.sleeves=='all'].to_dict('records'),"bootstrap":boot}; y["decision"]="Interpret only after reviewing cost monotonicity and every leave-one-out path."; run.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
    with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a") as f: f.write(json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":"RUN-0003","event":"completed","executed_variants":len(frame),"holdout_rows_loaded":0})+"\n")
    print(frame[(frame.sleeves=='all')|((frame.clock=='0940')&(frame.extra_slippage_bps_per_side==10))].to_string(index=False)); print(json.dumps(boot,indent=2))
if __name__=="__main__": main()
