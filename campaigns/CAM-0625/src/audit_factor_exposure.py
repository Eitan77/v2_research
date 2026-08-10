from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; sys.path.insert(0,str(CAM/"CAM-0600"/"src"))
from run_ensemble import paths,invvol_weights
from suite_core import load_panels

OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0009"
def main():
 OUT.mkdir(parents=True,exist_ok=True); z=paths(False); w=invvol_weights(z); portfolios={"equal":z.mean(axis=1),"causal_inverse_vol":(z*w).sum(axis=1)-w.diff().abs().sum(axis=1).fillna(0)*2/10000}; p=load_panels()["etf"]; bm={s:pd.Series(p.open_to_next_open_return[:,p.symbol_to_col[s]],index=p.dates) for s in ("SPY","QQQ","SMH","TLT")}; b=pd.concat(bm,axis=1).replace([np.inf,-np.inf],np.nan).fillna(0); rows=[]; residuals={}
 for name,s in portfolios.items():
  d=pd.concat([s.rename("portfolio"),b],axis=1,join="inner").dropna(); y=d.portfolio.to_numpy(); X=d[["SPY","QQQ","SMH","TLT"]].to_numpy(); design=np.column_stack([np.ones(len(X)),X]); coef=np.linalg.lstsq(design,y,rcond=None)[0]; pred=design@coef; resid=y-pred; residuals[name]=pd.Series(resid,index=d.index)
  ssr=float((resid**2).sum()); sst=float(((y-y.mean())**2).sum()); spy=d.SPY; bottom=spy<=spy.quantile(.05)
  rec={"rule":name,"days":len(d),"intercept_daily":float(coef[0]),"intercept_annualized_additive":float(coef[0]*252),"beta_SPY_multivariate":float(coef[1]),"beta_QQQ_multivariate":float(coef[2]),"beta_SMH_multivariate":float(coef[3]),"beta_TLT_multivariate":float(coef[4]),"r_squared":1-ssr/sst,"residual_net_simple_return":float(resid.sum()),"portfolio_return_SPY_up_days":float(d.loc[spy>0,'portfolio'].sum()),"portfolio_return_SPY_down_days":float(d.loc[spy<0,'portfolio'].sum()),"portfolio_return_SPY_bottom5pct_days":float(d.loc[bottom,'portfolio'].sum()),"correlation_SPY":float(d.portfolio.corr(d.SPY)),"correlation_QQQ":float(d.portfolio.corr(d.QQQ)),"correlation_SMH":float(d.portfolio.corr(d.SMH)),"correlation_TLT":float(d.portfolio.corr(d.TLT))}; rows.append(rec)
 pd.DataFrame(rows).to_csv(OUT/"factor_exposure.csv",index=False); pd.concat(residuals,axis=1).rename_axis("date").reset_index().to_parquet(OUT/"daily_residuals.parquet",index=False); report={"status":"completed","run_id":"RUN-0009","metrics":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n")
 run=CAM/"CAM-0625"/"runs"/"RUN-0009.yaml"; y=yaml.safe_load(run.read_text()); y["status"]="completed"; y["result"]=report; y["decision"]="Interpret residual return beside market beta; no pure-alpha or market-neutral claim."; run.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a") as f: f.write(json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":"RUN-0009","event":"completed","holdout_rows_loaded":0})+"\n")
 print(pd.DataFrame(rows).to_string(index=False))
if __name__=="__main__": main()
