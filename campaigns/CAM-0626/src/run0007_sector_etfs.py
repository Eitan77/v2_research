from __future__ import annotations
import json,multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import duckdb,numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0007';SYMS=['XLB','XLC','XLE','XLF','XLI','XLK','XLP','XLRE','XLU','XLV','XLY'];FORM={5:('09:30','09:35'),15:('09:40','09:45'),30:('09:55','10:00'),60:('10:25','10:30')};HOLDS=(30,60,120,'1550')
G_GROUPS=None;G_DATES=None
def add(t,m):return (pd.Timestamp('2000-01-01 '+t)+pd.Timedelta(minutes=m)).strftime('%H:%M')
def load():
 OUT.mkdir(parents=True,exist_ok=True);cache=OUT/'bars.parquet'
 if cache.exists():return pd.read_parquet(cache)
 c=duckdb.connect(r'D:\AlgoResearch\data\catalog.duckdb',read_only=True);c.execute('set threads=16');s=','.join("'"+x+"'" for x in SYMS);q=f'''select try_cast(session_date as date) date,symbol,strftime(bar_start_ts at time zone 'America/New_York','%H:%M') bar_time,arg_max(open,try_cast(ingested_at as timestamp)) as "open",arg_max(high,try_cast(ingested_at as timestamp)) as "high",arg_max(low,try_cast(ingested_at as timestamp)) as "low",arg_max(close,try_cast(ingested_at as timestamp)) as "close" from derived_bars_5m where try_cast(session_date as date) between date '2019-01-02' and date '2026-04-30' and feed='sip' and adjustment='raw' and bar_complete and symbol in ({s}) and strftime(bar_start_ts at time zone 'America/New_York','%H:%M') between '09:30' and '15:50' group by 1,2,3''';b=c.execute(q).fetchdf();c.close();b.date=pd.to_datetime(b.date);b.to_parquet(cache,index=False);return b
def weights(r,kind):
 res=r-r.mean()
 if kind=='all_proportional':return -res/np.abs(res).sum()
 k=1 if kind=='top1_each_side' else 2;o=np.argsort(res);w=np.zeros(len(r));w[o[:k]]=.5/k;w[o[-k:]]=-.5/k;return w
def calc(d,b):
 d=d.copy();d['net_pnl']=d.gross_pnl-d.gross*2*b/10000;eq=1+d.net_pnl.cumsum();pk=np.maximum.accumulate(np.r_[1.,eq])[1:];mo=d.set_index('date').net_pnl.resample('ME').sum();wk=d.set_index('date').net_pnl.resample('W-FRI').sum();return {'net_return':float(d.net_pnl.sum()),'max_drawdown':float(-(eq/pk-1).min()),'positive_days':int((d.net_pnl>0).sum()),'negative_days':int((d.net_pnl<0).sum()),'positive_weeks':int((wk>0).sum()),'negative_weeks':int((wk<0).sum()),'positive_months':int((mo>0).sum()),'negative_months':int((mo<0).sum()),'worst_month':float(mo.min()),'active_days':int((d.gross>0).sum())}
def simulate(groups,dates,fm,hold,kind,stop_pct):
 ft,en=FORM[fm];ex='15:50' if hold=='1550' else add(en,hold);daily=[];tr=[]
 for d in dates:
  obs=[]
  for s in SYMS:
   g=groups.get((d,s))
   if g is not None and {'09:30',ft,en,ex}.issubset(g.index):obs.append((s,np.log(float(g.loc[ft,'close'])/float(g.loc['09:30','open']))))
  if len(obs)<8:daily.append({'date':d,'gross_pnl':0.,'gross':0.});continue
  n=np.array([x[0] for x in obs]);r=np.array([x[1] for x in obs]);w=weights(r,kind);gp=0.
  for s,z in zip(n,w):
   if z==0:continue
   g=groups[(d,s)];entry=float(g.loc[en,'open']);out=float(g.loc[ex,'open']);reason='time_exit';out_time=ex
   if stop_pct is not None:
    stop=entry*(1-stop_pct/100 if z>0 else 1+stop_pct/100);path=g.loc[(g.index>=en)&(g.index<ex)]
    for _,bar in path.iterrows():
     if (z>0 and bar.low<=stop) or (z<0 and bar.high>=stop):out=min(stop,float(bar.open)) if z>0 else max(stop,float(bar.open));reason='stop';out_time=str(bar.name);break
   pnl=float(z*(out/entry-1));gp+=pnl;tr.append({'date':d,'symbol':s,'signed_weight':float(z),'weight':float(abs(z)),'side':'long' if z>0 else 'short','entry_time':en,'exit_time':out_time,'entry':entry,'exit':out,'reason':reason,'gross_pnl':pnl})
  daily.append({'date':d,'gross_pnl':gp,'gross':float(np.abs(w).sum())})
 return pd.DataFrame(daily),pd.DataFrame(tr)
def init_worker(path):
 global G_GROUPS,G_DATES
 b=pd.read_parquet(path);b.date=pd.to_datetime(b.date);G_GROUPS={(d,s):g.set_index('bar_time') for (d,s),g in b.groupby(['date','symbol'],sort=False)};G_DATES=sorted(b.date.unique())
def eval_task(task):
 fm,h,kind,stop=task;d,t=simulate(G_GROUPS,G_DATES,fm,h,kind,stop);vid=f'f{fm}_h{h}_{kind}_s{stop}';rows=[]
 for cost in (-1,0,1,2,5,10):m=calc(d,cost);m.update({'variant':vid,'formation_minutes':fm,'holding':str(h),'breadth':kind,'stop_pct':stop,'bps_per_side':cost,'gross_return':float(d.gross_pnl.sum()),'trade_legs':len(t),'stops':int((t.reason=='stop').sum())});rows.append(m)
 return rows
def main():
 global G_GROUPS,G_DATES
 b=load();path=OUT/'bars.parquet';dates=sorted(b.date.unique());tasks=[(fm,h,k,s) for fm in FORM for h in HOLDS for k in ('all_proportional','top1_each_side','top2_each_side') for s in (.5,1.,2.,None)]
 with ProcessPoolExecutor(max_workers=16,initializer=init_worker,initargs=(str(path),)) as pool:parts=list(pool.map(eval_task,tasks,chunksize=1))
 rows=[x for part in parts for x in part];f=pd.DataFrame(rows);f.to_parquet(OUT/'grid_metrics.parquet',index=False);lead=f[f.bps_per_side.eq(2)].sort_values(['net_return','max_drawdown'],ascending=[False,True]);best=str(lead.iloc[0].variant);r=lead.iloc[0];G_GROUPS={(dd,s):g.set_index('bar_time') for (dd,s),g in b.groupby(['date','symbol'],sort=False)};G_DATES=dates;bh='1550' if r.holding=='1550' else int(r.holding);bs=None if pd.isna(r.stop_pct) else float(r.stop_pct);d,t=simulate(G_GROUPS,G_DATES,int(r.formation_minutes),bh,str(r.breadth),bs);d['net_pnl']=d.gross_pnl-d.gross*4/10000;d.to_parquet(OUT/'best_daily.parquet',index=False);t.to_parquet(OUT/'best_trades.parquet',index=False);report={'status':'completed_bar_stage','planned_signal_variants':192,'executed_signal_variants':int(f.variant.nunique()),'executed_cost_cells':len(f),'best_2bps':lead.iloc[0].to_dict(),'positive_at_quote_gate':f[(f.bps_per_side.isin([-1,0,1,2]))&(f.net_return>0)].sort_values(['bps_per_side','net_return'],ascending=[False,False]).head(30).to_dict('records'),'loaded_rows':len(b),'expected_rows':len(dates)*len(SYMS)*77,'maximum_loaded_date':str(b.date.max().date()),'holdout_rows_loaded':int((b.date>pd.Timestamp('2026-04-30')).sum())};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n');print(lead.head(20)[['variant','net_return','max_drawdown','positive_days','negative_days','positive_weeks','negative_weeks','positive_months','negative_months','worst_month','trade_legs','stops']].to_string(index=False));print({k:report[k] for k in ('loaded_rows','expected_rows','maximum_loaded_date','holdout_rows_loaded')})
if __name__=='__main__':multiprocessing.freeze_support();main()
