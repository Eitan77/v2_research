from __future__ import annotations
import json,sys
from pathlib import Path
import duckdb,numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0600'/'src'))
from baseline_strategies import eligible
from suite_core import load_panels
OUT=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0001';START=pd.Timestamp('2024-05-01');END=pd.Timestamp('2026-04-30')
def fixed_metrics(d):
 eq=1+d.net_pnl.cumsum();peak=np.maximum.accumulate(np.r_[1.,eq.to_numpy()])[1:];dd=eq/peak-1;months=d.set_index('date').net_pnl.resample('ME').sum();weeks=d.set_index('date').net_pnl.resample('W-FRI').sum();years=d.groupby(d.date.dt.year).net_pnl.sum()
 return {'net_return':float(d.net_pnl.sum()),'max_drawdown':float(-dd.min()),'positive_days':int((d.net_pnl>0).sum()),'negative_days':int((d.net_pnl<0).sum()),'positive_weeks':int((weeks>0).sum()),'negative_weeks':int((weeks<0).sum()),'positive_months':int((months>0).sum()),'negative_months':int((months<0).sum()),'worst_day':float(d.net_pnl.min()),'worst_month':float(months.min()),'worst_year':float(years.min()),'active_days':int((d.gross>0).sum()),'average_daily_pnl':float(d.net_pnl.mean())}
def semantic_fixture():
 r=np.array([.03,-.01,.00]);res=r-r.mean();w=-res/np.abs(res).sum();assert abs(w.sum())<1e-12 and abs(np.abs(w).sum()-1)<1e-12 and abs(w[w>0].sum()-.5)<1e-12
 assert min(100*.98,97)==97 and max(100*1.02,103)==103
def build_selection(p,e):
 dates=pd.DatetimeIndex(p.dates);member=eligible(p);ret=pd.DataFrame(p.total_return_index,index=dates).pct_change().to_numpy();dv=pd.DataFrame(p.raw_close*p.volume,index=dates).rolling(20,min_periods=15).median().shift(1).to_numpy();q=e.total_return_index[:,e.symbol_to_col['QQQ']];qs=pd.Series(q,index=pd.DatetimeIndex(e.dates)).reindex(dates).ffill().pct_change().to_numpy();rows=[]
 for i,d in enumerate(dates):
  if d<START or d>END or i<61:continue
  y=qs[i-60:i];x=ret[i-60:i];valid=np.isfinite(x)&np.isfinite(y[:,None]);n=valid.sum(0);xx=np.where(valid,x,0);yy=np.where(valid,y[:,None],0);mx=xx.sum(0)/np.maximum(n,1);my=yy.sum(0)/np.maximum(n,1);dx=np.where(valid,x-mx,0);dy=np.where(valid,y[:,None]-my,0);den=np.sqrt((dx*dx).sum(0)*(dy*dy).sum(0));corr=np.where((n>=50)&(den>0),(dx*dy).sum(0)/den,np.nan);ok=np.flatnonzero(member[i]&np.isfinite(dv[i])&np.isfinite(corr));k=int(np.ceil(len(ok)*.5));liq=ok[np.argsort(dv[i,ok])[-k:]] if k else np.array([],int);pick=liq[np.argsort(corr[liq])[-min(20,len(liq)):]] if len(liq) else np.array([],int)
  for c in pick:rows.append({'date':d,'symbol':str(p.symbols[c]),'corr60':float(corr[c]),'median_dv20':float(dv[i,c])})
 return pd.DataFrame(rows)
def load_bars(pairs):
 c=duckdb.connect(r'D:\AlgoResearch\data\catalog.duckdb',read_only=True);c.execute(f"set temp_directory='{str((ROOT/'tmp'/'duckdb_cam0626').resolve()).replace(chr(92),'/')}'");c.execute("set threads=16");c.execute("set preserve_insertion_order=false");symbols=','.join("'"+s.replace("'","''")+"'" for s in sorted(pairs.symbol.unique()))
 q="""select try_cast(session_date as date) date,symbol,strftime(bar_start_ts at time zone 'America/New_York','%H:%M') bar_time,
 arg_max(open,try_cast(ingested_at as timestamp)) as "open",arg_max(high,try_cast(ingested_at as timestamp)) as "high",arg_max(low,try_cast(ingested_at as timestamp)) as "low",arg_max(close,try_cast(ingested_at as timestamp)) as "close",arg_max(volume,try_cast(ingested_at as timestamp)) as volume,max(available_at_ts) as available_at_ts
 from derived_bars_5m where try_cast(session_date as date) between date '2024-05-01' and date '2026-04-30' and feed='sip' and adjustment='raw' and bar_complete and symbol in (SYMBOLS)
 and strftime(bar_start_ts at time zone 'America/New_York','%H:%M') between '09:30' and '15:50' group by 1,2,3""".replace('SYMBOLS',symbols)
 d=c.execute(q).fetchdf();c.close();d.date=pd.to_datetime(d.date);d=d.merge(pairs[['date','symbol']],on=['date','symbol'],how='inner',validate='many_to_one');return d.sort_values(['date','symbol','bar_time'])
def simulate(pairs,bars,bps):
 trades=[];daily=[];grouped={(d,s):g.set_index('bar_time') for (d,s),g in bars.groupby(['date','symbol'],sort=False)}
 for d,sel in pairs.groupby('date',sort=True):
  formation=[]
  for s in sel.symbol:
   g=grouped.get((d,s));
   if g is None or not {'09:30','09:55','10:05','15:50'}.issubset(g.index):continue
   formation.append((s,float(np.log(g.loc['09:55','close']/g.loc['09:30','open']))))
  if len(formation)<10:daily.append({'date':d,'gross_pnl':0.,'net_pnl':0.,'gross':0.,'legs':0});continue
  names=np.array([x[0] for x in formation]);r=np.array([x[1] for x in formation]);res=r-r.mean();weights=-res/np.abs(res).sum()
  assert abs(weights.sum())<1e-10 and np.abs(weights).sum()<=1.0000001
  gp=0.;npnl=0.
  for s,w,signal in zip(names,weights,res):
   g=grouped[(d,s)];entry=float(g.loc['10:05','open']);exitp=float(g.loc['15:50','open']);reason='forced_1550';path=g.loc[(g.index>='10:05')&(g.index<'15:50')]
   stop=entry*(.98 if w>0 else 1.02)
   for _,bar in path.iterrows():
    hit=(w>0 and bar.low<=stop) or (w<0 and bar.high>=stop)
    if hit:exitp=min(stop,float(bar.open)) if w>0 else max(stop,float(bar.open));reason='protective_stop';break
   gross=float(w*(exitp/entry-1));cost=float(abs(w)*2*bps/10000);net=gross-cost;gp+=gross;npnl+=net;trades.append({'date':d,'symbol':s,'weight':float(w),'side':'long' if w>0 else 'short','signal_residual':float(signal),'entry':entry,'exit':exitp,'exit_reason':reason,'gross_pnl':gross,'cost':cost,'net_pnl':net})
  daily.append({'date':d,'gross_pnl':gp,'net_pnl':npnl,'gross':float(np.abs(weights).sum()),'legs':len(weights)})
 return pd.DataFrame(daily),pd.DataFrame(trades)
def main():
 semantic_fixture();OUT.mkdir(parents=True,exist_ok=True);ps=load_panels();p,e=ps['qqq'],ps['etf'];assert pd.Timestamp(p.dates.max())==END and pd.Timestamp(e.dates.max())==END
 pairs=build_selection(p,e);bars=load_bars(pairs);rows=[]
 for bps in (-1,0,1,2,5,10):
  d,t=simulate(pairs,bars,bps);m=fixed_metrics(d);m.update({'bps_per_side':bps,'trade_legs':len(t),'stops':int((t.exit_reason=='protective_stop').sum()),'long_net':float(t.loc[t.side=='long','net_pnl'].sum()),'short_net':float(t.loc[t.side=='short','net_pnl'].sum()),'top5_trade_positive_share':float(t.nlargest(5,'net_pnl').net_pnl.sum()/t.loc[t.net_pnl>0,'net_pnl'].sum())});rows.append(m)
  if bps==2:d.to_parquet(OUT/'daily_2bps.parquet',index=False);t.to_parquet(OUT/'trades_2bps.parquet',index=False)
 pairs.to_parquet(OUT/'selection.parquet',index=False);bars[['date','symbol','bar_time']].groupby('date').size().rename('rows').reset_index().to_parquet(OUT/'bar_coverage.parquet',index=False)
 expected=int(pairs.date.nunique()*20*77);report={'status':'completed_bar_stage','source_contract':'paper 3.9 equations 292-298 transferred to completed intraday returns','planned_variants':6,'executed_variants':len(rows),'selection_dates':int(pairs.date.nunique()),'selection_rows':len(pairs),'loaded_bar_rows':len(bars),'expected_selected_bar_rows':expected,'bar_row_coverage':len(bars)/expected,'maximum_loaded_date':str(bars.date.max().date()),'holdout_rows_loaded':int((bars.date>END).sum()),'metrics':rows};(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(pd.DataFrame(rows)[['bps_per_side','net_return','max_drawdown','positive_days','negative_days','positive_months','negative_months','worst_month','trade_legs','stops','long_net','short_net']].to_string(index=False));print({k:report[k] for k in ('selection_dates','loaded_bar_rows','bar_row_coverage','maximum_loaded_date','holdout_rows_loaded')})
if __name__=='__main__':main()
