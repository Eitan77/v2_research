from __future__ import annotations
import json
from pathlib import Path
import pandas as pd,yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0600"/"artifacts"/"RUN-0032"; RUN=CAM/"CAM-0600"/"runs"/"RUN-0032.yaml"
SPECS={
 "ma200_uncapped":(CAM/"CAM-0610"/"artifacts"/"RUN-0025"/"daily_0940_2bps.parquet",CAM/"CAM-0610"/"artifacts"/"RUN-0025"/"quote_metrics.csv",None),
 "ma200_corr08":(CAM/"CAM-0610"/"artifacts"/"RUN-0029"/"daily_0940_2bps.parquet",CAM/"CAM-0610"/"artifacts"/"RUN-0029"/"quote_metrics.csv",None),
 "ma50_200":(CAM/"CAM-0600"/"artifacts"/"RUN-0031"/"daily_CAM-0611_0940_2bps.parquet",CAM/"CAM-0600"/"artifacts"/"RUN-0031"/"quote_metrics.csv","CAM-0611"),
 "cluster_residual":(CAM/"CAM-0600"/"artifacts"/"RUN-0031"/"daily_CAM-0608_0940_2bps.parquet",CAM/"CAM-0600"/"artifacts"/"RUN-0031"/"quote_metrics.csv","CAM-0608"),
 "characteristic_residual":(CAM/"CAM-0600"/"artifacts"/"RUN-0031"/"daily_CAM-0609_0940_2bps.parquet",CAM/"CAM-0600"/"artifacts"/"RUN-0031"/"quote_metrics.csv","CAM-0609"),
 "triple_ma":(CAM/"CAM-0600"/"artifacts"/"RUN-0031"/"daily_CAM-0612_0940_2bps.parquet",CAM/"CAM-0600"/"artifacts"/"RUN-0031"/"quote_metrics.csv","CAM-0612"),
 "true_daily_alpha":(CAM/"CAM-0617"/"artifacts"/"RUN-0027"/"daily_CAM-0617_0940_2bps.parquet",CAM/"CAM-0617"/"artifacts"/"RUN-0027"/"quote_metrics.csv","CAM-0617"),
}
def dd(s):
 e=1+s.cumsum();return float(((e.cummax()-e)/e.cummax()).max())
def main():
 OUT.mkdir(parents=True,exist_ok=True); rows=[]
 for name,(daily_path,metrics_path,cid) in SPECS.items():
  d=pd.read_parquet(daily_path); d["date"]=pd.to_datetime(d.date); s=d.set_index("date").net_pnl.sort_index(); m=s.groupby(s.index.to_period("M")).sum(); q=pd.read_csv(metrics_path); q=q[(q.clock.astype(str).str.zfill(4)=="0940")&(q.extra_adverse_bps_per_side==2)]
  if cid: q=q[q.campaign_id==cid]
  if len(q)!=1: raise RuntimeError(f"metric row reconciliation {name}: {len(q)}")
  x=q.iloc[0]; rec={"candidate":name,"daily_rows":len(s),"minimum_date":str(s.index.min().date()),"maximum_date":str(s.index.max().date()),"net_recomputed":float(s.sum()),"net_saved":float(x.net_simple_return),"drawdown_recomputed":dd(s),"drawdown_saved":float(x.maximum_drawdown),"positive_months_recomputed":int((m>1e-12).sum()),"positive_months_saved":int(x.positive_months),"negative_months_recomputed":int((m<-1e-12).sum()),"negative_months_saved":int(x.negative_months),"trade_session_fraction":float(x.trade_session_fraction),"role_coverage":float(x.role_coverage),"holdout_rows_loaded":int((s.index>=pd.Timestamp("2026-05-01")).sum())}
  rec["passed"]=abs(rec["net_recomputed"]-rec["net_saved"])<1e-12 and abs(rec["drawdown_recomputed"]-rec["drawdown_saved"])<1e-12 and rec["positive_months_recomputed"]==rec["positive_months_saved"] and rec["negative_months_recomputed"]==rec["negative_months_saved"] and rec["role_coverage"]==1 and rec["holdout_rows_loaded"]==0; rows.append(rec)
 f=pd.DataFrame(rows); f.to_csv(OUT/"confirmation.csv",index=False); report={"status":"passed" if f.passed.all() else "failed","run_id":"RUN-0032","candidates":f.to_dict("records"),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n"); r=yaml.safe_load(RUN.read_text());r["status"]="completed";r["result"]=report;r["decision"]="Proceed to adaptations only for independently confirmed candidates." if f.passed.all() else "Stop interpretation and repair failed candidates.";RUN.write_text(yaml.safe_dump(r,sort_keys=False));print(f.to_string(index=False));print(report["status"])
if __name__=="__main__":main()
