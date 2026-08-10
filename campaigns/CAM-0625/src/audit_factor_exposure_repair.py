from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; sys.path.insert(0,str(CAM/"CAM-0600"/"src"))
from run_ensemble import paths,invvol_weights
from suite_core import load_panels

def main():
 old=CAM/"CAM-0625"/"runs"/"RUN-0009.yaml"; y=yaml.safe_load(old.read_text()); y["status"]="invalid"; y["result"]={"interpretation_blocker":"Reported residual sum is mechanically zero in OLS with an intercept; replace with intercept, simple betas, and downside capture."}; y["decision"]="Do not interpret RUN-0009; preserved under invalid artifact label."; old.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
 src=CAM/"CAM-0625"/"artifacts"/"RUN-0009"; dst=CAM/"CAM-0625"/"artifacts"/"RUN-0009_INVALID_OLS_RESIDUAL_SUM"; src.rename(dst)
 run=CAM/"CAM-0625"/"runs"/"RUN-0010.yaml"; plan={"run_id":"RUN-0010","campaign_id":"CAM-0625","parent_run":"RUN-0009","status":"planned","change":"Correct exposure attribution without the mechanically zero residual-sum metric.","reason":"RUN-0009 reported a non-informative OLS identity.","expected_effect":"Report multivariate intercept, simple betas, correlations, and downside capture.","configuration":{"portfolio_rules":["equal","causal_inverse_vol"],"benchmarks":["SPY","QQQ","SMH","TLT"],"regression":"daily OLS with intercept","cutoff":"2026-04-30","holdout_access":False},"result":None,"decision":None}; run.write_text(yaml.safe_dump(plan,sort_keys=False),encoding="utf-8")
 out=CAM/"CAM-0625"/"artifacts"/"RUN-0010"; out.mkdir(parents=True,exist_ok=True); z=paths(False); w=invvol_weights(z); portfolios={"equal":z.mean(axis=1),"causal_inverse_vol":(z*w).sum(axis=1)-w.diff().abs().sum(axis=1).fillna(0)*2/10000}; p=load_panels()["etf"]; b=pd.concat({s:pd.Series(p.open_to_next_open_return[:,p.symbol_to_col[s]],index=p.dates) for s in ("SPY","QQQ","SMH","TLT")},axis=1).fillna(0); rows=[]
 for name,s in portfolios.items():
  d=pd.concat([s.rename("portfolio"),b],axis=1,join="inner").dropna(); yy=d.portfolio.to_numpy(); X=d[["SPY","QQQ","SMH","TLT"]].to_numpy(); design=np.column_stack([np.ones(len(X)),X]); coef=np.linalg.lstsq(design,yy,rcond=None)[0]; resid=yy-design@coef; sst=((yy-yy.mean())**2).sum(); spy=d.SPY; bottom=spy<=spy.quantile(.05); rec={"rule":name,"days":len(d),"multivariate_intercept_daily":float(coef[0]),"multivariate_intercept_annualized_additive":float(coef[0]*252),"multivariate_r_squared":float(1-(resid**2).sum()/sst),"portfolio_return_SPY_up_days":float(d.loc[spy>0,'portfolio'].sum()),"portfolio_return_SPY_down_days":float(d.loc[spy<0,'portfolio'].sum()),"portfolio_return_SPY_bottom5pct_days":float(d.loc[bottom,'portfolio'].sum()),"mean_portfolio_return_SPY_down_days":float(d.loc[spy<0,'portfolio'].mean()),"mean_SPY_return_down_days":float(spy[spy<0].mean()),"downside_capture_ratio":float(d.loc[spy<0,'portfolio'].mean()/spy[spy<0].mean())}
  for sym in ("SPY","QQQ","SMH","TLT"):
   x=d[sym].to_numpy(); beta=float(np.cov(yy,x,ddof=1)[0,1]/np.var(x,ddof=1)); rec[f"simple_beta_{sym}"]=beta; rec[f"correlation_{sym}"]=float(d.portfolio.corr(d[sym]))
  rows.append(rec)
 frame=pd.DataFrame(rows); frame.to_csv(out/"factor_exposure.csv",index=False); report={"status":"completed","run_id":"RUN-0010","metrics":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}; (out/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n"); pth=CAM/"CAM-0625"/"runs"/"RUN-0010.yaml"; y=yaml.safe_load(pth.read_text()); y["status"]="completed"; y["result"]=report; y["decision"]="Positive residual intercept coexists with material equity/semiconductor beta and downside capture; no market-neutral or pure-alpha claim."; pth.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a") as f: f.write(json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":"RUN-0009","event":"invalid","reason":"mechanically zero OLS residual sum"})+"\n"+json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":"RUN-0010","event":"completed","holdout_rows_loaded":0})+"\n")
 print(frame.to_string(index=False))
if __name__=="__main__": main()
