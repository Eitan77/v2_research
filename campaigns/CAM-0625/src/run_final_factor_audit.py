from __future__ import annotations
import json
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0024"; DB=Path(r"D:\AlgoResearch\data\catalog.duckdb")
def audit(strategy,factors):
 x=pd.concat([strategy.rename("strategy"),factors],axis=1).dropna(); X=np.column_stack([np.ones(len(x)),x[["SPY","QQQ","SMH"]].to_numpy()]); y=x.strategy.to_numpy(); coef=np.linalg.lstsq(X,y,rcond=None)[0]; fitted=X@coef; residual=y-fitted; r2=1-float((residual**2).sum()/((y-y.mean())**2).sum()); spy_down=x.SPY<0; worst=x.SPY<=x.SPY.quantile(.05); return {"rows":len(x),"annualized_intercept":float(coef[0]*252),"betas":{"SPY":float(coef[1]),"QQQ":float(coef[2]),"SMH":float(coef[3])},"r_squared":r2,"downside_capture":float(x.loc[spy_down,"strategy"].sum()/abs(x.loc[spy_down,"SPY"].sum())),"worst_5pct_spy_days_strategy_sum":float(x.loc[worst,"strategy"].sum()),"worst_5pct_spy_days_spy_sum":float(x.loc[worst,"SPY"].sum()),"strategy_factor_correlations":x.corr().loc["strategy",["SPY","QQQ","SMH"]].to_dict()}
def main():
 OUT.mkdir(parents=True,exist_ok=True); paths={"full":CAM/"CAM-0625"/"artifacts"/"RUN-0022"/"lean3_plus_sector_full_daily.parquet","quote":CAM/"CAM-0625"/"artifacts"/"RUN-0023"/"daily_0940_2bps_extra.parquet"}; strategies={}
 for name,p in paths.items(): d=pd.read_parquet(p); d.date=pd.to_datetime(d.date); strategies[name]=d.set_index("date").net_pnl.sort_index()
 with duckdb.connect(str(DB),read_only=True) as con: bars=con.execute("SELECT symbol,date,close FROM bars_1d WHERE feed='sip' AND adjustment='split' AND symbol IN ('SPY','QQQ','SMH') AND date <= DATE '2026-04-30' ORDER BY date,symbol").df()
 bars.date=pd.to_datetime(bars.date); close=bars.pivot(index="date",columns="symbol",values="close"); factors=close.pct_change(); report={"status":"completed","run_id":"RUN-0024","audits":{name:audit(s,factors) for name,s in strategies.items()},"factor_maximum_date":str(factors.index.max().date()),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Descriptive development factor audit; close-to-close factors are approximate exposure controls."}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); path=CAM/"CAM-0625"/"runs"/"RUN-0024.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="completed"; run["result"]=report; run["decision"]="Use factor audit to qualify, not promote, the candidate."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":"RUN-0024","event":"completed","audits":report["audits"],"holdout_rows_loaded":0})+"\n")
 print(json.dumps(report,indent=2))
if __name__=="__main__": main()
