from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(Path(__file__).parent))
from run0001_baseline import load_bars
OUT=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0002';P1=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0001'
FORM={5:('09:30','09:35'),15:('09:40','09:45'),30:('09:55','10:00'),60:('10:25','10:30')};HOLDS=(30,60,120,'1550');BREADTH=('all','outer_half','outer_quartile')
def clock_add(t,m):q=pd.Timestamp('2000-01-01 '+t)+pd.Timedelta(minutes=m);return q.strftime('%H:%M')
def summarize(d,bps):
 d=d.copy();d['net_pnl']=d.gross_pnl-d.gross*2*bps/10000;eq=1+d.net_pnl.cumsum();pk=np.maximum.accumulate(np.r_[1.,eq])[1:];mo=d.set_index('date').net_pnl.resample('ME').sum();wk=d.set_index('date').net_pnl.resample('W-FRI').sum();return {'net_return':float(d.net_pnl.sum()),'max_drawdown':float(-(eq/pk-1).min()),'positive_days':int((d.net_pnl>0).sum()),'negative_days':int((d.net_pnl<0).sum()),'positive_weeks':int((wk>0).sum()),'negative_weeks':int((wk<0).sum()),'positive_months':int((mo>0).sum()),'negative_months':int((mo<0).sum()),'worst_month':float(mo.min()),'active_days':int((d.gross>0).sum())}
def weights(r,kind):
 res=r-r.mean()
 if kind=='all':return -res/np.abs(res).sum()
 n=max(1,len(r)//(4 if kind=='outer_quartile' else 2));lo=np.argsort(res)[:n];hi=np.argsort(res)[-n:];w=np.zeros(len(r));w[lo]=.5*np.abs(res[lo])/np.abs(res[lo]).sum();w[hi]=-.5*np.abs(res[hi])/np.abs(res[hi]).sum();return w
def simulate(pairs,bars,fm,hold,kind):
 ft,entry_t=FORM[fm];exit_t='15:50' if hold=='1550' else clock_add(entry_t,hold);groups={(d,s):g.set_index('bar_time') for (d,s),g in bars.groupby(['date','symbol'],sort=False)};daily=[];trades=[]
 for d,sel in pairs.groupby('date',sort=True):
  obs=[]
  for s in sel.symbol:
   g=groups.get((d,s));
   if g is not None and {'09:30',ft,entry_t,exit_t}.issubset(g.index):obs.append((s,np.log(float(g.loc[ft,'close'])/float(g.loc['09:30','open']))))
  if len(obs)<10:daily.append({'date':d,'gross_pnl':0.,'gross':0.});continue
  names=np.array([x[0] for x in obs]);r=np.array([x[1] for x in obs]);w=weights(r,kind);gp=0
  for s,z in zip(names,w):
   if abs(z)<1e-15:continue
   g=groups[(d,s)];entry=float(g.loc[entry_t,'open']);out=float(g.loc[exit_t,'open']);reason='time_exit';out_time=exit_t;stop=entry*(.98 if z>0 else 1.02);path=g.loc[(g.index>=entry_t)&(g.index<exit_t)]
   for _,bar in path.iterrows():
    if (z>0 and bar.low<=stop) or (z<0 and bar.high>=stop):out=min(stop,float(bar.open)) if z>0 else max(stop,float(bar.open));reason='stop';out_time=str(bar.name);break
   pnl=float(z*(out/entry-1));gp+=pnl;trades.append({'date':d,'symbol':s,'weight':float(z),'side':'long' if z>0 else 'short','entry_time':entry_t,'exit_time':out_time,'entry':entry,'exit':out,'reason':reason,'gross_pnl':pnl})
  daily.append({'date':d,'gross_pnl':gp,'gross':float(np.abs(w).sum())})
 return pd.DataFrame(daily),pd.DataFrame(trades)
def main():
 OUT.mkdir(parents=True,exist_ok=True);pairs=pd.read_parquet(P1/'selection_query.parquet');pairs.date=pd.to_datetime(pairs.date);cache=OUT/'selected_bars.parquet'
 if cache.exists():bars=pd.read_parquet(cache);bars.date=pd.to_datetime(bars.date)
 else:bars=load_bars(pairs);bars.to_parquet(cache,index=False)
 rows=[];saved={}
 for fm in FORM:
  for hold in HOLDS:
   for kind in BREADTH:
    d,t=simulate(pairs,bars,fm,hold,kind);vid=f'f{fm}_h{hold}_{kind}';saved[vid]=(d,t)
    for b in (-1,0,1,2,5,10):m=summarize(d,b);m.update({'variant':vid,'formation_minutes':fm,'holding':str(hold),'breadth':kind,'bps_per_side':b,'trade_legs':len(t),'stops':int((t.reason=='stop').sum()),'gross_return':float(d.gross_pnl.sum())});rows.append(m)
 frame=pd.DataFrame(rows);frame.to_parquet(OUT/'grid_metrics.parquet',index=False);leaders=frame[frame.bps_per_side.eq(2)].sort_values(['net_return','max_drawdown'],ascending=[False,True]);best=str(leaders.iloc[0].variant);d,t=saved[best];d['net_pnl']=d.gross_pnl-d.gross*4/10000;d.to_parquet(OUT/'best_daily_2bps.parquet',index=False);t.to_parquet(OUT/'best_trades.parquet',index=False)
 report={'status':'completed_bar_stage','planned_signal_variants':48,'executed_signal_variants':int(frame.variant.nunique()),'executed_cost_cells':len(frame),'best_2bps':leaders.iloc[0].to_dict(),'positive_at_quote_gate':leaders[leaders.net_return>0].head(12).to_dict('records'),'loaded_bar_rows':len(bars),'maximum_loaded_date':str(bars.date.max().date()),'holdout_rows_loaded':int((bars.date>pd.Timestamp('2026-04-30')).sum())};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n');print(leaders.head(15)[['variant','net_return','max_drawdown','positive_days','negative_days','positive_months','negative_months','worst_month','trade_legs','stops']].to_string(index=False));print({k:report[k] for k in ('loaded_bar_rows','maximum_loaded_date','holdout_rows_loaded')})
if __name__=='__main__':main()
