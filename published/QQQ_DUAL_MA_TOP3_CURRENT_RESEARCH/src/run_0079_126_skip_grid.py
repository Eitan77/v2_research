from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path[:0]=[str(Path(__file__).parent),str(ROOT/'campaigns'/'CAM-0600'/'src')]
from baseline_strategies import eligible,moving_average
from deep_strategies import liquid_mask
from suite_core import load_panels,trailing_return,weekly_indices,evaluate_weights
from run_0027_rank_challengers import select_equal,period_metrics
from run_0033_exit_overlays import base_context
from run_0067_last_year_breadth import extension
from run_0068_compounded_breadth import panel,simulate
OUT=ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0079';SKIPS=[0,1,5,10,15,21,30]
def main():
 OUT.mkdir(parents=True,exist_ok=True);p=load_panels()['qqq'];sig=weekly_indices(p.dates);mask=eligible(p)&(moving_average(p,50)>moving_average(p,200))&liquid_mask(p,.5);split=p.dates[int(len(p.dates)*.6)];dev=[]
 for s in SKIPS:
  score=trailing_return(p,126,s);w=select_equal(score,mask,sig)
  for bps in [2.,5.,10.]:
   m,d,*_=evaluate_weights(p,w,bps,holding='open_to_next_open',execution_lag=1);dev.append({'skip':s,'bps_per_side':bps,'return':m['net_simple_return'],'maximum_drawdown':m['maximum_drawdown'],'positive_months':m['positive_months'],'negative_months':m['negative_months'],'turnover':m['total_turnover'],**period_metrics(d.net_pnl,split)})
 dates,rets,_,fullmask,_=panel();hp,*_=base_context();ext_dates,_,ext_close,_,_,_=extension(hp);cl=np.vstack([hp.adj_close,ext_close]);tri=np.ones_like(cl);tri[:len(hp.dates)]=hp.total_return_index;last=hp.total_return_index[-1].copy();prev=hp.adj_close[-1].copy()
 for j in range(len(ext_dates)):
  i=len(hp.dates)+j;step=np.divide(cl[i],prev,out=np.ones(cl.shape[1]),where=np.isfinite(cl[i])&np.isfinite(prev)&(prev>0));last*=step;tri[i]=last;prev=cl[i]
 recent=[]
 for s in SKIPS:
  score=np.full_like(cl,np.nan)
  for i in range(126+s,len(dates)):score[i]=np.divide(tri[i-s],tri[i-s-126],out=np.full(cl.shape[1],np.nan),where=np.isfinite(tri[i-s])&np.isfinite(tri[i-s-126])&(tri[i-s-126]>0))-1
  _,m=simulate(dates,rets,score,fullmask,3);recent.append({'skip':s,'compounded_return':m['compounded_return'],'maximum_drawdown':m['maximum_drawdown'],'drawdown_peak':m['drawdown_peak'],'drawdown_trough':m['drawdown_trough']})
 a=pd.DataFrame(dev);b=pd.DataFrame(recent);a.to_csv(OUT/'development.csv',index=False);b.to_csv(OUT/'trailing_year_compounded.csv',index=False);report={'status':'completed','maximum_development_date':str(p.dates.max().date()),'maximum_descriptive_date':str(dates.max().date()),'holdout_use':'already_observed_descriptive_only','development':dev,'trailing_year':recent};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=lambda v:v.item() if hasattr(v,'item') else v)+'\n');print('DEVELOPMENT +2 BPS');print(a[a.bps_per_side.eq(2)].sort_values('skip').to_string(index=False));print('\nTRAILING YEAR COMPOUNDED');print(b.sort_values('skip').to_string(index=False))
if __name__=='__main__':main()
