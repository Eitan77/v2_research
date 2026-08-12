from __future__ import annotations
import sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];SRC=ROOT/'campaigns'/'CAM-0600'/'src';OUT=ROOT/'campaigns'/'CAM-0629'/'artifacts'/'RUN-0003';sys.path.insert(0,str(SRC))
from deep_strategies import build_deep_variants
from run_suite import _load_or_build_fundamentals
from suite_core import load_panels
def main():
 OUT.mkdir(parents=True,exist_ok=True);panels=load_panels();fund,_=_load_or_build_fundamentals(panels);v=next(x for x in build_deep_variants('CAM-0623',panels,fund) if x.variant_id=='sp500__chs_safe__top5__profitable__raw');p=v.panel;w=np.roll(v.weights,1,axis=0);w[0]=0;date=pd.DatetimeIndex(p.dates);ep=panels['etf'];j=ep.symbol_to_col['SPY'];close=pd.Series(ep.adj_close[:,j],index=ep.dates).reindex(date);open_=pd.Series(ep.adj_open[:,j],index=ep.dates).reindex(date);bad=((close.shift(1)/close.shift(6)-1)<-.03)&((open_/close.shift(1)-1)>0);overlay=w*np.where(bad.fillna(False),.25,1.)[:,None];rows=[];roles=[]
 for i,d in enumerate(date):
  for j,s in enumerate(p.symbols):
   if w[i,j]<=0:continue
   rows.append({'date':d,'symbol':str(s),'parent_weight':w[i,j],'overlay_weight':overlay[i,j],'bad_state':bool(bad.fillna(False).iloc[i])})
   for role,clock in [('entry0930','09:30'),('entry0940','09:40'),('exit1550','15:50')]:roles.append({'date':d,'symbol':str(s),'target_ts':pd.Timestamp(f'{d.date()} {clock}',tz='America/New_York').tz_convert('UTC'),'role':role})
 pd.DataFrame(rows).to_parquet(OUT/'weights.parquet',index=False);pd.DataFrame(roles).drop_duplicates(['symbol','target_ts','role']).to_parquet(OUT/'roles.parquet',index=False);print({'position_days':len(rows),'roles':len(roles),'max_date':str(date.max().date())})
if __name__=='__main__':main()
