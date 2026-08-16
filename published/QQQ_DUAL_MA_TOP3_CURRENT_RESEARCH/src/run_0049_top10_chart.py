from __future__ import annotations
import json,sys
from pathlib import Path
import duckdb,numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0600'/'src'));sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0611'/'src'))
from baseline_strategies import eligible
from suite_core import load_panels
import run_0026_aligned_sp500_dual as aligned
OUT=ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0049';COST=9.740340418

def extension_membership(universe,p,dates):
 if universe=='sp500':
  mem=pd.read_parquet(ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0026'/'aligned_membership'/'sp500_pit_membership_daily.parquet');mem.date=pd.to_datetime(mem.date)
  last=set(mem[(mem.date.eq('2026-04-30'))&mem.is_member].symbol.astype(str));return np.tile(np.array([str(s) in last for s in p.symbols]),(len(dates),1))
 con=duckdb.connect(r'D:\AlgoResearch\data\catalog.duckdb',read_only=True)
 m=con.execute("select try_cast(date as date) date,symbol,arg_max(is_member,try_cast(ingested_at as timestamp)) is_member from qqq_pit_membership_daily where try_cast(date as date) between date '2026-05-01' and date '2026-08-10' group by 1,2").fetchdf();con.close();m.date=pd.to_datetime(m.date)
 out=np.zeros((len(dates),p.n_symbols),bool)
 for s,c in p.symbol_to_col.items():out[:,c]=m[m.symbol.eq(s)].set_index('date').is_member.reindex(dates).ffill().fillna(False).to_numpy(bool)
 return out

def run_one(universe):
 p=load_panels()['qqq'] if universe=='qqq' else aligned.build()[0]
 src=(ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0042'/'daily_all_adjusted_apr_augtd.parquet') if universe=='qqq' else (ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0045'/'sp500_daily_all_adjusted_apr_augtd.parquet')
 raw=pd.read_parquet(src);raw.date=pd.to_datetime(raw.date);ext_dates=pd.DatetimeIndex(sorted(raw.loc[raw.date.between('2026-05-01','2026-08-10'),'date'].unique()))
 if ext_dates.max()!=pd.Timestamp('2026-08-10') or pd.Timestamp(p.dates.max())!=pd.Timestamp('2026-04-30'):raise RuntimeError('boundary mismatch')
 n,m=len(ext_dates),p.n_symbols;eo=np.full((n,m),np.nan);ec=np.full_like(eo,np.nan);edv=np.full_like(eo,np.nan)
 for s,c in p.symbol_to_col.items():
  g=raw[raw.symbol.eq(str(s))].set_index('date');a=g.loc[pd.Timestamp('2026-04-30')] if pd.Timestamp('2026-04-30') in g.index else None
  if a is None or not np.isfinite(p.adj_close[-1,c]):continue
  scale=float(p.adj_close[-1,c]/a.close)
  for i,d in enumerate(ext_dates):
   if d in g.index:eo[i,c]=float(g.loc[d].open)*scale;ec[i,c]=float(g.loc[d].close)*scale;edv[i,c]=float(g.loc[d].close)*float(g.loc[d].volume)
 dates=pd.DatetimeIndex(np.r_[p.dates,ext_dates]);op=np.vstack([p.adj_open,eo]);cl=np.vstack([p.adj_close,ec]);dv=np.vstack([p.raw_close*p.volume,edv]);member=np.vstack([eligible(p),extension_membership(universe,p,ext_dates)])
 close=pd.DataFrame(cl,index=dates);s50=close.rolling(50,min_periods=50).mean().to_numpy();s200=close.rolling(200,min_periods=200).mean().to_numpy();dv63=pd.DataFrame(dv,index=dates).rolling(63,min_periods=32).median().to_numpy()
 tri=np.full_like(cl,np.nan);tri[:len(p.dates)]=p.total_return_index;last=tri[len(p.dates)-1].copy();prev=cl[len(p.dates)-1]
 for i in range(len(p.dates),len(dates)):
  cur=cl[i];last=last*np.divide(cur,prev,out=np.ones(m),where=np.isfinite(cur)&np.isfinite(prev)&(prev>0));tri[i]=last;prev=cur
 periods=dates.to_period('W-FRI');signals=np.flatnonzero(np.r_[periods[1:]!=periods[:-1],True]);targets=np.zeros_like(cl)
 selections=[]
 for i in signals:
  if i<147:continue
  score=tri[i-21]/tri[i-147]-1;ready=member[i]&np.isfinite(cl[i])&(s50[i]>s200[i])&np.isfinite(score)&np.isfinite(dv63[i]);e=np.flatnonzero(ready);k=max(1,int(np.ceil(len(e)*.5))) if len(e) else 0;liq=e[np.argsort(dv63[i,e])[-k:]] if k else np.array([],int);chosen=liq[np.argsort(score[liq])[-min(10,len(liq)):]] if len(liq) else np.array([],int);targets[i,chosen]=1/len(chosen) if len(chosen) else 0;selections.append({'signal_date':str(dates[i].date()),'selected':[str(p.symbols[c]) for c in chosen]})
 executed=np.zeros_like(targets);current=np.zeros(m)
 sigset=set(signals.tolist())
 for i in range(1,len(dates)):
  if i-1 in sigset:current=targets[i-1].copy()
  executed[i]=current
 gross=np.zeros(len(dates));gross[:-1]=np.nansum(executed[:-1]*(np.divide(op[1:],op[:-1],out=np.ones_like(op[:-1]),where=np.isfinite(op[1:])&np.isfinite(op[:-1])&(op[:-1]>0))-1),axis=1);gross[-1]=np.nansum(executed[-1]*(np.divide(cl[-1],op[-1],out=np.ones(m),where=np.isfinite(cl[-1])&np.isfinite(op[-1])&(op[-1]>0))-1))
 turnover=np.abs(np.diff(executed,axis=0,prepend=np.zeros((1,m)))).sum(1);net=gross-turnover*COST/10000
 d=pd.DataFrame({'date':dates,'gross_pnl':gross,'turnover':turnover,'net_pnl':net});d=d[d.date.between('2025-08-11','2026-08-10')].reset_index(drop=True);d.to_parquet(OUT/f'{universe}_top10_daily.parquet',index=False)
 return d,selections

def main():
 OUT.mkdir(parents=True,exist_ok=True);report={'status':'completed','maximum_loaded_date':'2026-08-10','rows_after_authorized_end':0,'execution':'bar less frozen 9.740340418 bp per turnover','variants':{}}
 for u in ('qqq','sp500'):
  d,s=run_one(u);eq=1+d.net_pnl.cumsum();peak=np.maximum.accumulate(np.r_[1.,eq.to_numpy()])[1:];report['variants'][u]={'return_pct':float(100*d.net_pnl.sum()),'max_drawdown_pct':float(-100*(eq/peak-1).min()),'turnover':float(d.turnover.sum()),'signal_count':len([x for x in s if x['signal_date']>='2025-08-01'])}
 (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
