from __future__ import annotations
import importlib.util,json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];CAM=ROOT/'campaigns'/'CAM-0628';OUT=CAM/'artifacts'/'RUN-0002';sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0600'/'src'));import suite_core
spec=importlib.util.spec_from_file_location('r1',CAM/'src'/'run0001_grid.py');r1=importlib.util.module_from_spec(spec);spec.loader.exec_module(r1)
SLEEVES={'broad_indexes':['SPY','QQQ','DIA','IWM'],'sectors':['XLB','XLC','XLE','XLF','XLI','XLK','XLP','XLRE','XLU','XLV','XLY'],'technology':['QQQ','SMH','XLK'],'diversified_growth_defense':['SPY','QQQ','IWM','XLF','XLE','XLV','XLP']}
def main():
 OUT.mkdir(parents=True,exist_ok=True);p=suite_core.load_panels()['etf'];date=pd.DatetimeIndex(p.dates);rows=[];daily=[]
 for sleeve,symbols in SLEEVES.items():
  js=[int(np.where(p.symbols==s)[0][0]) for s in symbols];close=pd.DataFrame(p.adj_close[:,js],index=date,columns=symbols);oc=pd.DataFrame(p.open_to_close_return[:,js],index=date,columns=symbols);tri=pd.DataFrame(p.total_return_index[:,js],index=date,columns=symbols);ret=tri.pct_change()
  for window in (10,20,63):
   vol=ret.rolling(window,min_periods=window).std(ddof=1)*np.sqrt(252)
   for target in (.08,.12,.15,.20,.30):
    base=(target/vol).clip(upper=1).shift(1)/len(symbols)
    for trend in ('none','sma200'):
     w=base.copy()
     if trend=='sma200':w=w.where(close.shift(1)>close.rolling(200,min_periods=200).mean().shift(1),0.)
     gross=w.sum(axis=1);scale=pd.Series(np.where(gross>1,1/gross,1),index=date);w=w.mul(scale,axis=0)
     for bps in (-1,0,1,2,5,10):
      pnl=(w*oc).sum(axis=1)-w.sum(axis=1)*2*bps/10000;d=pd.DataFrame({'date':date,'weight':w.sum(axis=1),'pnl':pnl}).dropna();m=r1.met(d);m.update({'sleeve':sleeve,'vol_window':window,'target_vol':target,'trend':trend,'cost_bps_per_side':bps,'names':len(symbols)});rows.append(m)
      if bps==2:d['sleeve']=sleeve;d['vol_window']=window;d['target_vol']=target;d['trend']=trend;daily.append(d)
 grid=pd.DataFrame(rows);grid.to_parquet(OUT/'grid.parquet',index=False);pd.concat(daily,ignore_index=True).to_parquet(OUT/'daily_2bps.parquet',index=False);z=grid[grid.cost_bps_per_side.eq(2)].copy();z['min_block']=z.block_returns.apply(min);best=z.sort_values('net_return',ascending=False).iloc[0];robust=z.sort_values(['min_block','net_return'],ascending=False).iloc[0];report={'status':'completed','planned_signal_variants':120,'executed_signal_variants':120,'executed_cost_cells':len(grid),'best_2bps':{k:(v.item() if hasattr(v,'item') else v) for k,v in best.items()},'best_minimum_block_2bps':{k:(v.item() if hasattr(v,'item') else v) for k,v in robust.items()},'positive_all_blocks_at_2bps':int((z.min_block>0).sum()),'maximum_loaded_date':str(date.max().date()),'holdout_rows_loaded':0,'fixed_base':True,'broker_margin':False};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n');print(json.dumps(report,indent=2,default=str))
if __name__=='__main__':main()
