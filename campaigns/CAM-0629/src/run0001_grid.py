from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];SRC=ROOT/'campaigns'/'CAM-0600'/'src';OUT=ROOT/'campaigns'/'CAM-0629'/'artifacts'/'RUN-0001';sys.path.insert(0,str(SRC))
from deep_strategies import build_deep_variants
from run_suite import _load_or_build_fundamentals,_preflight
from run_sma_session_decomposition import add_consistency
from suite_core import evaluate_weights,load_panels
def main():
 OUT.mkdir(parents=True,exist_ok=True);panels=load_panels();pre=_preflight(panels);fund,coverage=_load_or_build_fundamentals(panels);pre['fundamental_coverage']=coverage;base=build_deep_variants('CAM-0623',panels,fund);rows=[];daily_rows=[]
 for v in base:
  p=v.panel;oc=np.nan_to_num(p.open_to_close_return,nan=0.);actual=np.roll(v.weights,1,axis=0);actual[0]=0;sleeve=(actual*oc).sum(axis=1);controls=[('unscaled',v.weights)]
  for window in (20,63,126):
   forecast=pd.Series(sleeve).rolling(window,min_periods=window).std(ddof=1).to_numpy()*np.sqrt(252)
   for target in (.08,.10,.12,.15):
    scale=np.minimum(1,np.divide(target,forecast,out=np.zeros_like(forecast),where=np.isfinite(forecast)&(forecast>0)));controls.append((f'vol{window}_target{int(target*100)}',v.weights*scale[:,None]))
  for control,w in controls:
   if (w<-1e-12).any() or np.abs(w).sum(1).max()>1+1e-9:raise RuntimeError('weight integrity')
   for cost in (-1.,0.,1.,2.,5.,10.):
    m,d,_,_,_=evaluate_weights(p,w,cost,holding='open_to_close',execution_lag=1);m,wk,mo=add_consistency(m,d);m['block_returns']=[float(d.iloc[ix].net_pnl.sum()) for ix in np.array_split(np.arange(len(d)),3)];m.update({'variant_id':v.variant_id,'control':control});rows.append(m)
    if cost==2:d=d.copy();d['variant_id']=v.variant_id;d['control']=control;daily_rows.append(d)
 frame=pd.DataFrame(rows);frame.to_parquet(OUT/'grid.parquet',index=False);pd.concat(daily_rows,ignore_index=True).to_parquet(OUT/'daily_2bps.parquet',index=False);z=frame[frame.cost_bps_per_side.eq(2)].copy();z['minimum_block']=z.block_returns.apply(lambda x:min(x));best=z.sort_values('net_simple_return',ascending=False).iloc[0];recent=z.sort_values('recent12_return',ascending=False).iloc[0];robust=z.sort_values(['minimum_block','net_simple_return'],ascending=False).iloc[0];report={'status':'completed','base_variants':len(base),'signal_variants':len(base)*13,'executed_cost_cells':len(frame),'best_2bps':{k:(v.item() if hasattr(v,'item') else v) for k,v in best.items()},'best_recent12_2bps':{k:(v.item() if hasattr(v,'item') else v) for k,v in recent.items()},'best_minimum_block_2bps':{k:(v.item() if hasattr(v,'item') else v) for k,v in robust.items()},'positive_all_blocks_at_2bps':int((z.minimum_block>0).sum()),'preflight':pre,'maximum_loaded_date':'2026-04-30','holdout_rows_loaded':0,'broker_margin':False};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n');print(json.dumps(report,indent=2,default=str))
if __name__=='__main__':main()
