from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd,yaml
ROOT=Path(__file__).resolve().parents[3]; SRC=ROOT/"campaigns"/"CAM-0600"/"src"; sys.path.insert(0,str(SRC)); sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0610"/"src")); sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0617"/"src"))
from baseline_strategies import moving_average
from deep_strategies import active_trend_rank,concentrate_positive,liquid_mask,trailing_vol
from run_correlation_capped_ma import build as corr_build
from run_daily_frequency_repairs import variants as repair_variants
from run_true_daily_alpha import solve as alpha_solve
from run_suite import _load_or_build_fundamentals
from suite_core import CAMPAIGNS,COSTS_BPS,evaluate_weights,load_panels,save_variant
OUT=CAMPAIGNS/"CAM-0600"/"artifacts"/"RUN-0033"; RUN=CAMPAIGNS/"CAM-0600"/"runs"/"RUN-0033.yaml"

def bases(panels,f):
 p=panels["sp500"]; ma=active_trend_rank(p,p.adj_close>moving_average(p,200),np.arange(p.n_dates),10,"momentum"); corr=corr_build(p,.8)
 wanted={"cluster_residual":("CAM-0608","sp500__daily_residual_r5__top3__trend1"),"characteristic_residual":("CAM-0609","qqq__daily_residual_r10__top3__trend1"),"ma50_200":("CAM-0611","sp500__ma50_200__daily__top10__momentum"),"triple_ma":("CAM-0612","sp500__ma3_10_21__daily__top3__momentum")}; out={"ma200_uncapped":(p,ma),"ma200_corr08":(p,corr)}
 for name,(cid,vid) in wanted.items():
  hit=[x for x in repair_variants(cid,panels,f) if x[0]==vid]; assert len(hit)==1; out[name]=(hit[0][1],hit[0][2])
 q=panels["qqq"]; out["true_daily_alpha"]=(q,concentrate_positive(alpha_solve(q,20,5),10,liquid_mask(q,.5))); return out

def renorm(w,gross):
 s=np.abs(w).sum(1); return np.divide(w,gross[:,None]*s[:,None],out=np.zeros_like(w),where=s[:,None]>0)
def invvol(p,w):
 v=trailing_vol(p,63); x=np.where((w>0)&np.isfinite(v)&(v>1e-8),w/v,0); return renorm(x,np.abs(w).sum(1))
def persist(w,n):
 mask=w>0
 for lag in range(1,n): mask[lag:]&=w[:-lag]>0; mask[:lag]=False
 x=np.where(mask,w,0); return renorm(x,np.abs(w).sum(1))
def vol_target(p,w,target=.15):
 ex=np.zeros_like(w);ex[1:]=w[:-1]; pnl=np.sum(ex*np.nan_to_num(p.open_to_next_open_return,nan=0),axis=1); rv=pd.Series(pnl).rolling(63,min_periods=40).std(ddof=1).shift(1).to_numpy()*np.sqrt(252); scale=np.minimum(1,np.divide(target,rv,out=np.zeros_like(rv),where=rv>1e-8)); return w*scale[:,None]
def band(w,threshold=.05):
 out=np.zeros_like(w); prev=np.zeros(w.shape[1])
 for i in range(len(w)):
  desired=w[i]
  if np.abs(desired-prev).sum()>=threshold: prev=desired.copy()
  out[i]=prev
 return out
def overlays(p,w): return {"base":w,"inverse_vol63":invvol(p,w),"persistence2":persist(w,2),"persistence3":persist(w,3),"vol_target_15":vol_target(p,w),"turnover_band_05":band(w),"inverse_vol63_persistence2":persist(invvol(p,w),2)}

def main():
 OUT.mkdir(parents=True,exist_ok=True); panels=load_panels();f,_=_load_or_build_fundamentals(panels);rows=[]
 for name,(p,w) in bases(panels,f).items():
  for overlay,x in overlays(p,w).items():
   vid=f"{name}__{overlay}"
   for cost in COSTS_BPS:
    m,d,mo,y,s=evaluate_weights(p,x,cost,holding="open_to_next_open",execution_lag=1); rec={"candidate":name,"overlay":overlay,"variant_id":vid,**m};rows.append(rec);save_variant(OUT,f"{vid}__cost_{cost:g}bps",rec,d,mo,y,s,save_detail=float(cost)==2)
 frame=pd.DataFrame(rows);frame.to_csv(OUT/"variant_metrics.csv",index=False);a=frame[frame.cost_bps_per_side==2].copy(); base=a[a.overlay=="base"][["candidate","net_simple_return","maximum_drawdown","recent12_positive_months","recent12_average_month","top5_symbol_positive_share"]].rename(columns={c:"base_"+c for c in ["net_simple_return","maximum_drawdown","recent12_positive_months","recent12_average_month","top5_symbol_positive_share"]}); a=a.merge(base,on="candidate");a["pareto_improvement"]=(a.net_simple_return>=a.base_net_simple_return)&(a.maximum_drawdown<=a.base_maximum_drawdown)&(a.recent12_positive_months>=a.base_recent12_positive_months);a["consistency_improvement"]=(a.recent12_positive_months>a.base_recent12_positive_months)&(a.maximum_drawdown<=a.base_maximum_drawdown*1.10)&(a.net_simple_return>0);a.to_csv(OUT/"overlay_comparison_2bps.csv",index=False)
 selected=[]
 for name,g in a.groupby("candidate"):
  eligible=g[(g.maximum_drawdown<=g.base_maximum_drawdown*1.10)&(g.recent12_positive_months>=g.base_recent12_positive_months)&(g.net_simple_return>0)]; pick=eligible.sort_values(["recent12_positive_months","recent12_average_month","maximum_drawdown"],ascending=[False,False,True]).iloc[0];selected.append(pick.to_dict())
 report={"status":"completed","run_id":"RUN-0033","selected":selected,"pareto_improvements":int(a.pareto_improvement.sum()),"consistency_improvements":int(a.consistency_improvement.sum()),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin":False};(OUT/"execution_report.json").write_text(json.dumps(report,indent=2,default=str)+"\n");r=yaml.safe_load(RUN.read_text());r["status"]="completed";r["result"]=report;r["decision"]="Send only material low-cost improvements to exact quote replay and retain base when overlays dilute the edge.";RUN.write_text(yaml.safe_dump(r,sort_keys=False));print(pd.DataFrame(selected)[["candidate","overlay","net_simple_return","maximum_drawdown","recent12_average_month","recent12_positive_months","top5_symbol_positive_share"]].to_string(index=False));print(json.dumps({"pareto":report["pareto_improvements"],"consistency":report["consistency_improvements"]}))
if __name__=="__main__":main()
