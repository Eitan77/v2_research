from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd,yaml
ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"))
from baseline_strategies import alpha_holdings
from deep_strategies import concentrate_positive,liquid_mask,trend_mask
from suite_core import CAMPAIGNS,COSTS_BPS,evaluate_weights,load_panels,save_variant
OUT=CAMPAIGNS/"CAM-0617"/"artifacts"/"RUN-0026"; RUN=CAMPAIGNS/"CAM-0617"/"runs"/"RUN-0026.yaml"

def solve(p,history,expected_days):
 holdings,names=alpha_holdings(p); asset=np.nan_to_num(p.open_to_next_open_return,nan=0); ar=np.column_stack([np.sum(h*asset,axis=1) for h in holdings]); combo=np.zeros_like(p.adj_close)
 for i in range(history+expected_days+1,p.n_dates):
  R=ar[i-history-1:i]; X=R-R.mean(0); sigma=X.std(0,ddof=1); valid=np.isfinite(sigma)&(sigma>1e-10)
  if valid.sum()<=history: continue
  Y=(X[:,valid]/sigma[valid])[:-1]; load=(Y-Y.mean(1,keepdims=True))[:-1].T; exp=ar[i-expected_days:i,valid].mean(0)/sigma[valid]; residual=exp-load@np.linalg.lstsq(load,exp,rcond=None)[0]; aw=residual/sigma[valid]; scale=np.abs(aw).sum()
  if scale<=0: continue
  stock=np.zeros(p.n_symbols)
  for coeff,idx in zip(aw/scale,np.flatnonzero(valid)): stock+=coeff*holdings[int(idx)][i]
  if np.abs(stock).sum()>0: combo[i]=stock/np.abs(stock).sum()
 return combo

def main():
 OUT.mkdir(parents=True,exist_ok=True); p=load_panels()["qqq"]; rows=[]
 for hist in (20,60):
  raw=solve(p,hist,5)
  for trend in (False,True):
   mask=liquid_mask(p,.5)&(trend_mask(p,200) if trend else True); w=concentrate_positive(raw,10,mask); vid=f"qqq__alpha_M{hist}_E5__true_daily__top10__trend{int(trend)}"
   for cost in COSTS_BPS:
    m,d,mo,y,s=evaluate_weights(p,w,cost,holding="open_to_next_open",execution_lag=1); rec={"campaign_id":"CAM-0617","run_id":"RUN-0026","variant_id":vid,**m,"holding":"open_to_next_open"}; rows.append(rec); save_variant(OUT,f"{vid}__cost_{cost:g}bps",rec,d,mo,y,s,save_detail=float(cost)==2)
 f=pd.DataFrame(rows); f.to_csv(OUT/"variant_metrics.csv",index=False); a=f[f.cost_bps_per_side==2].sort_values(["recent12_positive_months","recent12_average_month"],ascending=False); best=a.iloc[0].to_dict(); report={"status":"completed","run_id":"RUN-0026","selected":best,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2,default=str)+"\n"); r=yaml.safe_load(RUN.read_text()); r["status"]="completed";r["result"]=report;r["decision"]="Quote replay only if low-cost profit and consistency survive true daily turnover.";RUN.write_text(yaml.safe_dump(r,sort_keys=False));print(a.to_string(index=False))
if __name__=="__main__":main()
