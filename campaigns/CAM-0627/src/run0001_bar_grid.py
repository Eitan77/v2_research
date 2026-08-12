from __future__ import annotations
import json,multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import duckdb,numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0627'/'artifacts'/'RUN-0001';PAIRS=(('SPY','IVV'),('SPY','VOO'),('IVV','VOO'),('QQQ','QQQM'));G=None
def load():
 OUT.mkdir(parents=True,exist_ok=True);p=OUT/'bars_1m.parquet'
 if p.exists():return pd.read_parquet(p)
 sy=','.join("'"+s+"'" for s in sorted(set(sum(([a,b] for a,b in PAIRS),[]))));c=duckdb.connect(r'D:\AlgoResearch\data\catalog.duckdb',read_only=True);c.execute('set threads=16');q=f'''select date,symbol,try_cast(timestamp as timestamptz) ts,arg_max(open,try_cast(ingested_at as timestamp)) as "open",arg_max(high,try_cast(ingested_at as timestamp)) as "high",arg_max(low,try_cast(ingested_at as timestamp)) as "low",arg_max(close,try_cast(ingested_at as timestamp)) as "close" from bars_1m where date between date '2021-01-04' and date '2026-04-30' and feed='sip' and adjustment='raw' and symbol in ({sy}) and strftime(try_cast(timestamp as timestamptz) at time zone 'America/New_York','%H:%M') between '09:30' and '15:59' group by 1,2,3''';b=c.execute(q).fetchdf();c.close();b.date=pd.to_datetime(b.date);b.ts=pd.to_datetime(b.ts,utc=True);b.to_parquet(p,index=False);return b
def contexts(b):
 out={}
 for a,z in PAIRS:
  x=b[b.symbol.eq(a)].merge(b[b.symbol.eq(z)],on=['date','ts'],suffixes=('_a','_b'));x=x.sort_values(['date','ts']);days=[];prev=None
  for d,g in x.groupby('date',sort=True):
   g=g.sort_values('ts');clock=g.ts.dt.tz_convert('America/New_York').dt.strftime('%H:%M');trade=g[clock.le('15:50')]
   if len(trade)<300:prev=float(np.log(g.close_a.iloc[-1]/g.close_b.iloc[-1]));continue
   ropen=float(np.log(trade.open_a.iloc[0]/trade.open_b.iloc[0]));rclose=np.log(trade.close_a.to_numpy()/trade.close_b.to_numpy());days.append({'date':pd.Timestamp(d),'ts':trade.ts.to_numpy(),'oa':trade.open_a.to_numpy(float),'ob':trade.open_b.to_numpy(float),'r':rclose,'opening':ropen,'prior':prev});prev=float(np.log(g.close_a.iloc[-1]/g.close_b.iloc[-1]))
  out[f'{a}_{z}']=days
 return out
def init(path):
 global G;G=contexts(pd.read_parquet(path))
def simulate(pair,anchor,threshold,hold,stop):
 rows=[]
 for day in G[pair]:
  base=day[anchor]
  if base is None or not np.isfinite(base):continue
  div=(day['r']-base)*1e4;n=len(div);i=1;armed=True
  while i<n-2:
   if not armed:
    if abs(div[i])<threshold/2:armed=True
    i+=1;continue
   if abs(div[i])<threshold:i+=1;continue
   sign=1 if div[i]>0 else -1;entry_i=i+1
   if entry_i>=n-1:break
   entry_div=(np.log(day['oa'][entry_i]/day['ob'][entry_i])-base)*1e4;end=min(entry_i+hold-1,n-2);reason='max_hold';sig=div[i];exit_signal=end
   for j in range(entry_i,end+1):
    if div[j]*sign<=0:exit_signal=j;reason='convergence';break
    if (div[j]-entry_div)*sign>=stop:exit_signal=j;reason='spread_stop';break
   exit_i=min(exit_signal+1,n-1);ea,eb,xa,xb=day['oa'][entry_i],day['ob'][entry_i],day['oa'][exit_i],day['ob'][exit_i]
   pnl=.5*((1-xa/ea)+(xb/eb-1)) if sign>0 else .5*((xa/ea-1)+(1-xb/eb));rows.append({'date':day['date'],'pair':pair,'anchor':anchor,'threshold_bps':threshold,'hold_minutes':hold,'stop_bps':stop,'signal_bps':float(sig),'entry_ts':pd.Timestamp(day['ts'][entry_i]),'exit_ts':pd.Timestamp(day['ts'][exit_i]),'rich_leg':pair.split('_')[0] if sign>0 else pair.split('_')[1],'gross_pnl':float(pnl),'reason':reason});armed=reason=='convergence';i=exit_i+1

 return pd.DataFrame(rows,columns=['date','pair','anchor','threshold_bps','hold_minutes','stop_bps','signal_bps','entry_ts','exit_ts','rich_leg','gross_pnl','reason'])
def metrics(t,b):
 dates=pd.date_range('2021-01-04','2026-04-30',freq='B');d=t.groupby('date').gross_pnl.sum().reindex(dates,fill_value=0)-t.groupby('date').size().reindex(dates,fill_value=0)*2*b/10000;eq=1+d.cumsum();pk=np.maximum.accumulate(np.r_[1.,eq])[1:];mo=d.resample('ME').sum();wk=d.resample('W-FRI').sum();recent=d[d.index>=pd.Timestamp('2025-05-01')];return {'net_return':float(d.sum()),'recent_12m_return':float(recent.sum()),'max_drawdown':float(-(eq/pk-1).min()),'positive_days':int((d>0).sum()),'negative_days':int((d<0).sum()),'active_days':int((t.groupby('date').size()>0).sum()),'positive_weeks':int((wk>0).sum()),'negative_weeks':int((wk<0).sum()),'positive_months':int((mo>0).sum()),'negative_months':int((mo<0).sum()),'worst_month':float(mo.min()),'trades':len(t),'trade_win_rate':float((t.gross_pnl>2*b/10000).mean()) if len(t) else 0.,'average_net_trade':float(t.gross_pnl.mean()-2*b/10000) if len(t) else 0.}
def task(x):
 p,a,th,h,s=x;t=simulate(p,a,th,h,s);vid=f'{p}_{a}_t{th}_h{h}_s{s}';rows=[]
 for b in (-1,0,1,2,5,10):m=metrics(t,b);m.update({'variant':vid,'pair':p,'anchor':a,'threshold_bps':th,'hold_minutes':h,'stop_bps':s,'cost_bps_per_side':b,'gross_return':float(t.gross_pnl.sum()),'stop_count':int((t.reason=='spread_stop').sum()) if len(t) else 0});rows.append(m)
 return rows
def main():
 b=load();path=OUT/'bars_1m.parquet';tasks=[(f'{a}_{z}',an,th,h,s) for a,z in PAIRS for an in ('prior','opening') for th in (2,5,10,20,40) for h in (1,2,5,15,30) for s in (20,50,100)]
 with ProcessPoolExecutor(max_workers=16,initializer=init,initargs=(str(path),)) as pool:parts=list(pool.map(task,tasks,chunksize=1))
 f=pd.DataFrame([r for p in parts for r in p]);f.to_parquet(OUT/'grid_metrics.parquet',index=False);lead=f[f.cost_bps_per_side.eq(2)].sort_values(['net_return','max_drawdown'],ascending=[False,True]);r=lead.iloc[0];global G;G=contexts(b);t=simulate(str(r.pair),str(r.anchor),int(r.threshold_bps),int(r.hold_minutes),int(r.stop_bps));t.to_parquet(OUT/'best_trades.parquet',index=False);report={'status':'completed_bar_stage','planned_signal_variants':600,'executed_signal_variants':int(f.variant.nunique()),'executed_cost_cells':len(f),'best_2bps':r.to_dict(),'positive_quote_gate_count':int(len(f[(f.cost_bps_per_side.isin([-1,0,1,2]))&(f.net_return>0)])),'loaded_rows':len(b),'minimum_loaded_date':str(b.date.min().date()),'maximum_loaded_date':str(b.date.max().date()),'holdout_rows_loaded':int((b.date>pd.Timestamp('2026-04-30')).sum()),'pairs':list(map(list,PAIRS))};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n');print(lead.head(25)[['variant','net_return','recent_12m_return','max_drawdown','active_days','positive_days','negative_days','positive_weeks','negative_weeks','positive_months','negative_months','worst_month','trades','trade_win_rate','average_net_trade']].to_string(index=False));print({k:report[k] for k in ('loaded_rows','minimum_loaded_date','maximum_loaded_date','holdout_rows_loaded')})
if __name__=='__main__':multiprocessing.freeze_support();main()
