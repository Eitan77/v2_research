from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src")); sys.path.insert(0,str(Path(__file__).parent))
from baseline_strategies import eligible,moving_average
from deep_strategies import liquid_mask
from suite_core import evaluate_weights,load_panels,trailing_return,weekly_indices
from run_0027_rank_challengers import select_equal
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0029"; COST=9.740340417752536
def main():
 OUT.mkdir(parents=True,exist_ok=True); p=load_panels()["qqq"]
 if str(p.dates.max().date())!="2026-04-30" or p.readiness.get("holdout_rows_loaded_total",0)!=0: raise RuntimeError("readiness failed")
 signals=weekly_indices(p.dates); mask=eligible(p)&(moving_average(p,50)>moving_average(p,200))&liquid_mask(p,.5); score=trailing_return(p,126,21); rows=[]
 for n in range(1,21):
  w=select_equal(score,mask,signals,n); m,d,monthly,yearly,symbols=evaluate_weights(p,w,COST,holding="open_to_next_open",execution_lag=1); recent=d.net_pnl.loc[d.index>=pd.Timestamp("2025-05-01")]
  rows.append({"top_n":n,"total_return":float(d.net_pnl.sum()),"recent12_return":float(recent.sum()),"maximum_drawdown":float(m["maximum_drawdown"]),"turnover":float(m["total_turnover"]),"positive_months":int(m["positive_months"]),"negative_months":int(m["negative_months"])})
 pd.DataFrame(rows).to_csv(OUT/"breadth_curve.csv",index=False)
 report={"status":"completed","breadths":20,"execution_model":"bar plus frozen 7.740340418 bp average quote slippage plus 2 bp adverse per turnover","control_top3_return":rows[2]["total_return"],"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"metrics":rows}; (OUT/"report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); print(pd.DataFrame(rows).to_string(index=False))
if __name__=="__main__": main()
