from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"
SLEEVES={
 "momentum":("CAM-0600","RUN-0008","sp500__mom63_skip0__top3__liquid__panic1"),
 "multifactor":("CAM-0604","RUN-0008","sp500__value_quality__top20__trend0"),
 "ibs":("CAM-0621","RUN-0010","etf__ibs30__top5__hold3__trend1"),
 "distress":("CAM-0624","RUN-0008","qqq__chs_safe__top5__liquid__target8"),
}

def dd(s):
    e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max())

def metrics(name,s,w=None):
    m=s.groupby(s.index.to_period("M")).sum(); pos=s.clip(lower=0).sort_values(ascending=False); total=pos.sum()
    return {"variant_id":name,"net_simple_return":float(s.sum()),"maximum_drawdown":dd(s),"active_days":int((s.abs()>1e-12).sum()),"green_days":int((s>1e-12).sum()),"red_days":int((s<-1e-12).sum()),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"monthly_average":float(m.mean()),"monthly_median":float(m.median()),"monthly_std":float(m.std(ddof=1)),"worst_month":float(m.min()),"best_month":float(m.max()),"top5_day_positive_share":float(pos.head(5).sum()/total) if total>0 else None,"average_weights":None if w is None else {c:float(w[c].mean()) for c in w}}

def paths(quote=False):
    out={}
    for name,(cid,run,variant) in SLEEVES.items():
        if quote:
            qrun="RUN-0011" if cid=="CAM-0621" else "RUN-0009"; p=CAM/cid/"artifacts"/qrun/"daily_0940_2bps_extra.parquet"
        else:
            safe=(variant+"__cost_2bps").replace("/","_").replace(":","_"); p=CAM/cid/"artifacts"/run/"variants"/safe/"daily.parquet"
        d=pd.read_parquet(p); d["date"]=pd.to_datetime(d.date); out[name]=d.set_index("date").net_pnl
    z=pd.concat(out,axis=1).fillna(0).sort_index()
    if not quote: z=z.loc[max(pd.Timestamp("2021-05-03"),z.index.min()):pd.Timestamp("2026-04-30")]
    return z

def invvol_weights(z):
    vol=z.rolling(126,min_periods=63).std(ddof=1).shift(1); inv=1/vol.replace(0,np.nan); raw=inv.div(inv.sum(axis=1),axis=0).clip(.10,.40); raw=raw.div(raw.sum(axis=1),axis=0)
    month=z.index.to_period("M"); signal=raw.groupby(month).head(1); w=signal.reindex(z.index).ffill().fillna(.25)
    return w

def evaluate(z,label):
    fixed={"equal":np.array([.25,.25,.25,.25]),"defensive_fixed":np.array([.30,.35,.20,.15])}; rows=[]; daily={}; weights={}
    for name,a in fixed.items(): daily[name]=(z*a).sum(axis=1); weights[name]=pd.DataFrame(np.tile(a,(len(z),1)),index=z.index,columns=z.columns)
    w=invvol_weights(z); alloc_cost=w.diff().abs().sum(axis=1).fillna(0)*2/10000; daily["causal_inverse_vol"]=(z*w).sum(axis=1)-alloc_cost; weights["causal_inverse_vol"]=w
    out=CAM/"CAM-0625"/"artifacts"/"RUN-0002"; out.mkdir(parents=True,exist_ok=True)
    for name,s in daily.items():
        rec={"sample":label,**metrics(name,s,weights[name])}; rows.append(rec); s.rename("net_pnl").rename_axis("date").reset_index().to_parquet(out/f"daily_{label}_{name}.parquet",index=False); s.groupby(s.index.to_period("M")).sum().rename("net_pnl").rename_axis("month").reset_index().to_csv(out/f"monthly_{label}_{name}.csv",index=False)
    return rows

def main():
    full=paths(False); quote=paths(True); rows=evaluate(full,"full_history")+evaluate(quote,"quote_0940_2bps_extra"); frame=pd.DataFrame(rows); out=CAM/"CAM-0625"/"artifacts"/"RUN-0002"; frame.to_csv(out/"ensemble_metrics.csv",index=False)
    corr=full.corr(); corr.to_csv(out/"sleeve_daily_correlation.csv")
    report={"status":"completed","run_id":"RUN-0002","full_history_start":str(full.index.min().date()),"maximum_loaded_date":str(full.index.max().date()),"quote_start":str(quote.index.min().date()),"quote_end":str(quote.index.max().date()),"holdout_rows_loaded":0,"broker_margin":False,"compounding":False,"metrics":json.loads(frame.to_json(orient="records")),"sleeve_correlations":json.loads(corr.to_json())}; (out/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n")
    run=CAM/"CAM-0625"/"runs"/"RUN-0002.yaml"; y=yaml.safe_load(run.read_text()); y["status"]="completed"; y["result"]={"variant_count":3,"full_history_start":report["full_history_start"],"maximum_loaded_date":report["maximum_loaded_date"],"quote_window":[report["quote_start"],report["quote_end"]],"holdout_rows_loaded":0}; y["decision"]="Development-only ensemble evidence; audit all allocations and preserve without promotion."; run.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
    with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":"RUN-0002","event":"completed","variants":3,"holdout_rows_loaded":0})+"\n")
    print(frame.to_string(index=False)); print(corr.to_string())
if __name__=="__main__": main()
