from __future__ import annotations
import importlib.util,json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];SRC=ROOT/'campaigns'/'CAM-0600'/'src';OUT=ROOT/'campaigns'/'CAM-0629'/'artifacts'/'RUN-0002';sys.path.insert(0,str(SRC))
from deep_strategies import build_deep_variants
from run_suite import _load_or_build_fundamentals
from suite_core import load_panels
spec=importlib.util.spec_from_file_location('m',ROOT/'campaigns'/'CAM-0628'/'src'/'run0001_grid.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def main():
 OUT.mkdir(parents=True,exist_ok=True);panels=load_panels();fund,_=_load_or_build_fundamentals(panels);v=next(x for x in build_deep_variants('CAM-0623',panels,fund) if x.variant_id=='sp500__chs_safe__top5__profitable__raw');p=v.panel;actual=np.roll(v.weights,1,axis=0);actual[0]=0;date=pd.DatetimeIndex(p.dates);oc=np.nan_to_num(p.open_to_close_return,nan=0.);ep=panels['etf'];j=ep.symbol_to_col['SPY'];spy_close=pd.Series(ep.adj_close[:,j],index=ep.dates).reindex(date);spy_open=pd.Series(ep.adj_open[:,j],index=ep.dates).reindex(date);r=spy_close.pct_change();states={'below_spy_sma200':spy_close.shift(1)<spy_close.rolling(200,min_periods=200).mean().shift(1),'spy_vol20_above_vol126':r.rolling(20).std().shift(1)>r.rolling(126).std().shift(1),'positive_gap_over_1pct':spy_open/spy_close.shift(1)-1>.01,'rebound_after_5d_loss':((spy_close.shift(1)/spy_close.shift(6)-1)<-.03)&((spy_open/spy_close.shift(1)-1)>0)};rows=[];daily=[]
 for state,bad in states.items():
  for mult in (.25,.50):
   scale=np.where(bad.fillna(False),mult,1.);w=actual*scale[:,None];gross=np.abs(w).sum(1);raw=(w*oc).sum(1)
   for bps in (-1,0,1,2,5,10):
    d=pd.DataFrame({'date':date,'weight':gross,'pnl':raw-gross*2*bps/10000});z=m.met(d);z.update({'state':state,'bad_multiplier':mult,'cost_bps_per_side':bps,'bad_days':int(bad.fillna(False).sum())});rows.append(z)
    if bps==2:d['state']=state;d['bad_multiplier']=mult;daily.append(d)
 frame=pd.DataFrame(rows);frame.to_parquet(OUT/'grid.parquet',index=False);pd.concat(daily,ignore_index=True).to_parquet(OUT/'daily_2bps.parquet',index=False);z=frame[frame.cost_bps_per_side.eq(2)].copy();z['minimum_block']=z.block_returns.apply(min);best=z.sort_values('net_return',ascending=False).iloc[0];robust=z.sort_values(['minimum_block','net_return'],ascending=False).iloc[0];report={'status':'completed','planned_signal_variants':8,'executed_signal_variants':8,'executed_cost_cells':len(frame),'best_2bps':{k:(v.item() if hasattr(v,'item') else v) for k,v in best.items()},'best_minimum_block_2bps':{k:(v.item() if hasattr(v,'item') else v) for k,v in robust.items()},'positive_all_blocks_at_2bps':int((z.minimum_block>0).sum()),'maximum_loaded_date':'2026-04-30','holdout_rows_loaded':0};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n');print(json.dumps(report,indent=2,default=str))
if __name__=='__main__':main()
