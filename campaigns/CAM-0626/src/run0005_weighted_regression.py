from __future__ import annotations
import json,sys,multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(Path(__file__).parent));sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0600'/'src'))
from suite_core import load_panels
from run0002_timing_selectivity import FORM,HOLDS,clock_add,summarize
OUT=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0005';P1=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0001';P2=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0002'
MODELS=('unit','invvar','invvar_beta')
G_PAIRS=None;G_BARS=None;G_GROUPS=None

def enrich(pairs):
 p=load_panels()['qqq'];dates=pd.DatetimeIndex(p.dates);ret=pd.DataFrame(p.total_return_index,index=dates).pct_change();vol=ret.rolling(20,min_periods=15).std().shift(1);q=ret['QQQ'] if 'QQQ' in ret.columns else None
 # QQQ is an ETF, not a constituent panel column; use equal-weight panel return as a causal market proxy for beta neutralization.
 market=ret.mean(axis=1);cov=ret.rolling(60,min_periods=50).cov(market).shift(1);var=market.rolling(60,min_periods=50).var().shift(1);beta=cov.div(var,axis=0)
 cols=pd.Index(p.symbols.astype(str));vol.columns=cols;beta.columns=cols
 long=pd.concat([vol.stack().rename('vol20'),beta.stack().rename('beta60')],axis=1).reset_index();long.columns=['date','symbol','vol20','beta60'];long.date=pd.to_datetime(long.date)
 return pairs.merge(long,on=['date','symbol'],how='left',validate='one_to_one')

def regress_weights(r,vol,beta,model):
 good=np.isfinite(r)&np.isfinite(vol)&(vol>0)&np.isfinite(beta);out=np.zeros(len(r))
 if good.sum()<10:return out
 y=r[good];z=np.ones(len(y)) if model=='unit' else 1/np.square(vol[good]);X=np.ones((len(y),1)) if model!='invvar_beta' else np.column_stack([np.ones(len(y)),beta[good]])
 gram=X.T@(z[:,None]*X)
 try:coef=np.linalg.solve(gram,X.T@(z*y))
 except np.linalg.LinAlgError:return out
 tw=z*(y-X@coef);den=np.abs(tw).sum()
 if den>0:out[np.flatnonzero(good)]=-tw/den
 return out

def simulate(pairs,bars,fm,hold,model):
 ft,entry_t=FORM[fm];exit_t='15:50' if hold=='1550' else clock_add(entry_t,hold);groups=G_GROUPS if G_GROUPS is not None else {(d,s):g.set_index('bar_time') for (d,s),g in bars.groupby(['date','symbol'],sort=False)};daily=[];trades=[]
 for d,sel in pairs.groupby('date',sort=True):
  obs=[]
  for x in sel.itertuples():
   g=groups.get((d,x.symbol))
   if g is not None and {'09:30',ft,entry_t,exit_t}.issubset(g.index):obs.append((x.symbol,np.log(float(g.loc[ft,'close'])/float(g.loc['09:30','open'])),x.vol20,x.beta60))
  if len(obs)<10:daily.append({'date':d,'gross_pnl':0.,'gross':0.});continue
  names=np.array([x[0] for x in obs]);r=np.array([x[1] for x in obs]);vol=np.array([x[2] for x in obs]);beta=np.array([x[3] for x in obs]);w=regress_weights(r,vol,beta,model);gp=0.
  for s,z in zip(names,w):
   if abs(z)<1e-15:continue
   g=groups[(d,s)];entry=float(g.loc[entry_t,'open']);out=float(g.loc[exit_t,'open']);reason='time_exit';out_time=exit_t;stop=entry*(.98 if z>0 else 1.02);path=g.loc[(g.index>=entry_t)&(g.index<exit_t)]
   for _,bar in path.iterrows():
    if (z>0 and bar.low<=stop) or (z<0 and bar.high>=stop):out=min(stop,float(bar.open)) if z>0 else max(stop,float(bar.open));reason='stop';out_time=str(bar.name);break
   pnl=float(z*(out/entry-1));gp+=pnl;trades.append({'date':d,'symbol':s,'weight':float(z),'side':'long' if z>0 else 'short','entry_time':entry_t,'exit_time':out_time,'entry':entry,'exit':out,'reason':reason,'gross_pnl':pnl})
  daily.append({'date':d,'gross_pnl':gp,'gross':float(np.abs(w).sum())})
 return pd.DataFrame(daily),pd.DataFrame(trades)

def init_worker(pair_path,bar_path):
 global G_PAIRS,G_BARS,G_GROUPS
 G_PAIRS=pd.read_parquet(pair_path);G_PAIRS.date=pd.to_datetime(G_PAIRS.date);G_BARS=pd.read_parquet(bar_path);G_BARS.date=pd.to_datetime(G_BARS.date);G_GROUPS={(d,s):g.set_index('bar_time') for (d,s),g in G_BARS.groupby(['date','symbol'],sort=False)}

def eval_task(task):
 fm,hold,model=task;d,t=simulate(G_PAIRS,G_BARS,fm,hold,model);vid=f'f{fm}_h{hold}_{model}';rows=[]
 for b in (-1,0,1,2,5,10):
  m=summarize(d,b);m.update({'variant':vid,'formation_minutes':fm,'holding':str(hold),'model':model,'bps_per_side':b,'trade_legs':len(t),'stops':int((t.reason=='stop').sum()),'gross_return':float(d.gross_pnl.sum())});rows.append(m)
 return rows

def main():
 global G_PAIRS,G_BARS,G_GROUPS
 OUT.mkdir(parents=True,exist_ok=True);risk_path=OUT/'selection_risk_inputs.parquet'
 if risk_path.exists():pairs=pd.read_parquet(risk_path);pairs.date=pd.to_datetime(pairs.date)
 else:pairs=pd.read_parquet(P1/'selection.parquet');pairs.date=pd.to_datetime(pairs.date);pairs=enrich(pairs);pairs.to_parquet(risk_path,index=False)
 bars=pd.read_parquet(P2/'selected_bars.parquet');bars.date=pd.to_datetime(bars.date);tasks=[(fm,h,m) for fm in FORM for h in HOLDS for m in MODELS]
 with ProcessPoolExecutor(max_workers=16,initializer=init_worker,initargs=(str(risk_path),str(P2/'selected_bars.parquet'))) as pool:parts=list(pool.map(eval_task,tasks,chunksize=1))
 rows=[x for part in parts for x in part];frame=pd.DataFrame(rows);frame.to_parquet(OUT/'grid_metrics.parquet',index=False);leaders=frame[frame.bps_per_side.eq(2)].sort_values(['net_return','max_drawdown'],ascending=[False,True]);best=str(leaders.iloc[0].variant);r=leaders.iloc[0];G_PAIRS=pairs;G_BARS=bars;G_GROUPS={(dd,s):g.set_index('bar_time') for (dd,s),g in bars.groupby(['date','symbol'],sort=False)};bh='1550' if r.holding=='1550' else int(r.holding);d,t=simulate(pairs,bars,int(r.formation_minutes),bh,str(r.model));d['net_pnl']=d.gross_pnl-d.gross*4/10000;d.to_parquet(OUT/'best_daily_2bps.parquet',index=False);t.to_parquet(OUT/'best_trades.parquet',index=False)
 report={'status':'completed_bar_stage','planned_signal_variants':48,'executed_signal_variants':int(frame.variant.nunique()),'executed_cost_cells':len(frame),'risk_input_missing_rows':int(pairs[['vol20','beta60']].isna().any(axis=1).sum()),'best_2bps':leaders.iloc[0].to_dict(),'positive_at_quote_gate':frame[(frame.bps_per_side.isin([-1,0,1,2]))&(frame.net_return>0)].sort_values(['bps_per_side','net_return'],ascending=[False,False]).head(30).to_dict('records'),'maximum_loaded_date':str(bars.date.max().date()),'holdout_rows_loaded':int((bars.date>pd.Timestamp('2026-04-30')).sum())};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n');print(leaders.head(20)[['variant','net_return','max_drawdown','positive_days','negative_days','positive_weeks','negative_weeks','positive_months','negative_months','worst_month','trade_legs','stops']].to_string(index=False));print({k:report[k] for k in ('risk_input_missing_rows','maximum_loaded_date','holdout_rows_loaded')})
if __name__=='__main__':multiprocessing.freeze_support();main()
