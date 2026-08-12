from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];CAM=ROOT/'campaigns'/'CAM-0628';OUT=CAM/'artifacts'/'RUN-0001';sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0600'/'src'));import suite_core
SYMBOLS=['SPY','QQQ','DIA','IWM','MDY','RSP','SMH','XLK','XLF','XLE','XLV','XLY','XLP','XLI','XLU','XLB','XLC','XLRE','TQQQ','QLD','SSO','UPRO','SOXL']
def met(d):
 d=d.copy();eq=1+d.pnl.cumsum();pk=np.maximum.accumulate(np.r_[1.,eq])[1:];dd=eq/pk-1;mo=d.set_index('date').pnl.resample('ME').sum();wk=d.set_index('date').pnl.resample('W-FRI').sum();recent=d[d.date>=pd.Timestamp('2025-05-01')];pos=d.loc[d.pnl>0,'pnl'].sum();top=d.nlargest(5,'pnl').pnl.sum();blocks=np.array_split(np.arange(len(d)),3);return {'net_return':float(d.pnl.sum()),'recent12_return':float(recent.pnl.sum()),'max_drawdown':float(-dd.min()),'active_days':int((d.weight>0).sum()),'positive_days':int((d.pnl>0).sum()),'negative_days':int((d.pnl<0).sum()),'positive_weeks':int((wk>0).sum()),'negative_weeks':int((wk<0).sum()),'positive_months':int((mo>0).sum()),'negative_months':int((mo<0).sum()),'worst_month':float(mo.min()),'top5_positive_day_share':float(top/pos) if pos>0 else 0.,'block_returns':[float(d.iloc[ix].pnl.sum()) for ix in blocks],'average_weight':float(d.weight.mean())}
def main():
 OUT.mkdir(parents=True,exist_ok=True);p=suite_core.load_panels()['etf'];available=[s for s in SYMBOLS if s in set(p.symbols)];missing=[s for s in SYMBOLS if s not in set(p.symbols)];rows=[];daily=[]
 for symbol in available:
  j=int(np.where(p.symbols==symbol)[0][0]);date=pd.DatetimeIndex(p.dates);close=pd.Series(p.adj_close[:,j],index=date);oc=pd.Series(p.open_to_close_return[:,j],index=date);tri=pd.Series(p.total_return_index[:,j],index=date);ret=tri.pct_change()
  for window in (10,20,63):
   vol=ret.rolling(window,min_periods=window).std(ddof=1)*np.sqrt(252)
   for target in (.08,.12,.15,.20,.30):
    base=(target/vol).clip(upper=1).shift(1)
    for trend in ('none','sma50','sma200'):
     w=base.copy()
     if trend!='none':
      n=int(trend[3:]);eligible=(close.shift(1)>close.rolling(n,min_periods=n).mean().shift(1));w=w.where(eligible,0.)
     for bps in (-1,0,1,2,5,10):
      d=pd.DataFrame({'date':date,'weight':w,'ret':oc}).dropna();d['pnl']=d.weight*d.ret-d.weight*2*bps/10000;m=met(d);m.update({'symbol':symbol,'vol_window':window,'target_vol':target,'trend':trend,'cost_bps_per_side':bps});rows.append(m)
      if bps==2:
       d['symbol']=symbol;d['vol_window']=window;d['target_vol']=target;d['trend']=trend;daily.append(d[['date','symbol','vol_window','target_vol','trend','weight','pnl']])
 grid=pd.DataFrame(rows);grid.to_parquet(OUT/'grid.parquet',index=False);pd.concat(daily,ignore_index=True).to_parquet(OUT/'daily_2bps.parquet',index=False);active=grid[(grid.cost_bps_per_side.eq(2))&(grid.active_days>=200)];best=active.sort_values('net_return',ascending=False).iloc[0];best_recent=active.sort_values('recent12_return',ascending=False).iloc[0];report={'status':'completed','planned_signal_variants':len(SYMBOLS)*3*5*3,'executed_signal_variants':len(available)*3*5*3,'executed_cost_cells':len(rows),'available_symbols':available,'missing_symbols':missing,'best_2bps':{k:(v.item() if hasattr(v,'item') else v) for k,v in best.items()},'best_recent12_2bps':{k:(v.item() if hasattr(v,'item') else v) for k,v in best_recent.items()},'data_readiness':p.readiness,'maximum_loaded_date':str(p.dates.max().date()),'holdout_rows_loaded':0,'fixed_base':True,'broker_margin':False};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n');print(json.dumps(report,indent=2,default=str))
if __name__=='__main__':main()
