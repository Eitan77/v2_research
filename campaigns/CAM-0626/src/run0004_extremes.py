from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(Path(__file__).parent))
from run0002_timing_selectivity import FORM,HOLDS,clock_add,summarize
OUT=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0004';P1=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0001';P2=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0002'

def simulate(pairs,bars,fm,hold,k):
 ft,entry_t=FORM[fm];exit_t='15:50' if hold=='1550' else clock_add(entry_t,hold)
 groups={(d,s):g.set_index('bar_time') for (d,s),g in bars.groupby(['date','symbol'],sort=False)};daily=[];trades=[]
 for d,sel in pairs.groupby('date',sort=True):
  obs=[]
  for s in sel.symbol:
   g=groups.get((d,s))
   if g is not None and {'09:30',ft,entry_t,exit_t}.issubset(g.index):obs.append((s,np.log(float(g.loc[ft,'close'])/float(g.loc['09:30','open']))))
  if len(obs)<2*k:daily.append({'date':d,'gross_pnl':0.,'gross':0.});continue
  names=np.array([x[0] for x in obs]);res=np.array([x[1] for x in obs]);res-=res.mean();order=np.argsort(res)
  w=np.zeros(len(res));w[order[:k]]=0.5/k;w[order[-k:]]=-0.5/k;gp=0.
  for s,z in zip(names,w):
   if z==0:continue
   g=groups[(d,s)];entry=float(g.loc[entry_t,'open']);out=float(g.loc[exit_t,'open']);reason='time_exit';out_time=exit_t;stop=entry*(.98 if z>0 else 1.02);path=g.loc[(g.index>=entry_t)&(g.index<exit_t)]
   for _,bar in path.iterrows():
    if (z>0 and bar.low<=stop) or (z<0 and bar.high>=stop):out=min(stop,float(bar.open)) if z>0 else max(stop,float(bar.open));reason='stop';out_time=str(bar.name);break
   pnl=float(z*(out/entry-1));gp+=pnl;trades.append({'date':d,'symbol':s,'weight':float(abs(z)),'signed_weight':float(z),'side':'long' if z>0 else 'short','entry_time':entry_t,'exit_time':out_time,'entry':entry,'exit':out,'reason':reason,'gross_pnl':pnl})
  daily.append({'date':d,'gross_pnl':gp,'gross':float(np.abs(w).sum())})
 return pd.DataFrame(daily),pd.DataFrame(trades)

def main():
 OUT.mkdir(parents=True,exist_ok=True);pairs=pd.read_parquet(P1/'selection_query.parquet');pairs.date=pd.to_datetime(pairs.date);bars=pd.read_parquet(P2/'selected_bars.parquet');bars.date=pd.to_datetime(bars.date)
 rows=[];saved={}
 for fm in FORM:
  for hold in HOLDS:
   for k in (1,2,3,4):
    d,t=simulate(pairs,bars,fm,hold,k);vid=f'f{fm}_h{hold}_k{k}';saved[vid]=(d,t)
    for b in (-1,0,1,2,5,10):
     m=summarize(d,b);m.update({'variant':vid,'formation_minutes':fm,'holding':str(hold),'names_per_side':k,'bps_per_side':b,'trade_legs':len(t),'stops':int((t.reason=='stop').sum()),'gross_return':float(d.gross_pnl.sum())});rows.append(m)
 frame=pd.DataFrame(rows);frame.to_parquet(OUT/'grid_metrics.parquet',index=False);leaders=frame[frame.bps_per_side.eq(2)].sort_values(['net_return','max_drawdown'],ascending=[False,True]);best=str(leaders.iloc[0].variant);d,t=saved[best];d['net_pnl']=d.gross_pnl-d.gross*4/10000;d.to_parquet(OUT/'best_daily_2bps.parquet',index=False);t.to_parquet(OUT/'best_trades.parquet',index=False)
 report={'status':'completed_bar_stage','planned_signal_variants':64,'executed_signal_variants':int(frame.variant.nunique()),'executed_cost_cells':len(frame),'best_2bps':leaders.iloc[0].to_dict(),'positive_at_quote_gate':frame[(frame.bps_per_side.isin([-1,0,1,2]))&(frame.net_return>0)].sort_values(['bps_per_side','net_return'],ascending=[False,False]).head(30).to_dict('records'),'maximum_loaded_date':str(bars.date.max().date()),'holdout_rows_loaded':int((bars.date>pd.Timestamp('2026-04-30')).sum())}
 (OUT/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n');print(leaders.head(20)[['variant','net_return','max_drawdown','positive_days','negative_days','positive_weeks','negative_weeks','positive_months','negative_months','worst_month','trade_legs','stops']].to_string(index=False));print({k:report[k] for k in ('maximum_loaded_date','holdout_rows_loaded')})
if __name__=='__main__':main()
