from __future__ import annotations
import argparse, json
from pathlib import Path
import duckdb, numpy as np, pandas as pd

H=tuple(range(5,386,5)); COSTS=(0,2,5,10,25,50); GAPS=(25,50,100,200,400)
SYMS=('SPY','QQQ','IWM','DIA','TQQQ','SQQQ','SOXL','SOXS','SMH','XLK','XLF','XLE','TLT','GLD','USO','ARKK','VOO','IVV','VIXY')

def dd(x):
 e=np.cumsum(x); return float(np.min(e-np.maximum.accumulate(e))) if len(e) else np.nan

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--catalog',default='D:/AlgoResearch/data/catalog.duckdb'); ap.add_argument('--out',required=True); a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
 hs=','.join(f"max(case when f.m=580+{h} then f.close/e.entry_open-1 end) as fwd_{h}" for h in H)
 ph=','.join(f"max(case when f.m between 580 and 580+{h} then f.high/e.entry_open-1 end) as hi_{h},min(case when f.m between 580 and 580+{h} then f.low/e.entry_open-1 end) as lo_{h}" for h in H)
 marks=','.join('?' for _ in SYMS)
 sql=f'''with b as (select symbol,session_date,cast(timestamp as timestamptz) at time zone 'America/New_York' et,open,high,low,close from research_matrix where timeframe='5m' and session_date between '2019-06-21' and '2025-12-31' and symbol in ({marks})), x as (select *,extract(hour from et)*60+extract(minute from et) m from b), d0 as (select symbol,session_date,max(open) filter(where m=570) session_open,max(close) filter(where m=955) session_close from x group by 1,2), d as (select *,lag(session_close) over(partition by symbol order by session_date) prev_close from d0), e0 as (select s.symbol,s.session_date,d.session_open,d.prev_close,d.session_open/d.prev_close-1 gap,s.close signal_close,e.open entry_open,e.m entry_m from x s join d using(symbol,session_date) join x e using(symbol,session_date) where s.m=575 and e.m=580 and d.session_open>0 and d.prev_close>0 and e.open>0), e as (select *,row_number() over(partition by session_date order by gap asc,symbol) day_rank from e0), f as (select * from x where m between 585 and 955), p as (select e.symbol,e.session_date,e.gap,e.signal_close,e.entry_open,{hs},{ph} from e join f using(symbol,session_date) where e.day_rank<=19 and f.m>=e.entry_m group by e.symbol,e.session_date,e.gap,e.signal_close,e.entry_open)
 select * from p order by session_date,symbol'''
 con=duckdb.connect(a.catalog,read_only=True); con.execute('set threads=16'); con.execute("set temp_directory='D:/AlgoResearch/work/duck_tmp'")
 try: df=con.execute(sql,list(SYMS)).fetchdf()
 finally: con.close()
 df.to_parquet(out/'gap_event_paths.parquet',index=False)
 rows=[]
 for g in GAPS:
  q=df[df.gap<=-g/10000].copy(); q['score']=-q.gap; q=q.sort_values(['session_date','score','symbol'],ascending=[True,False,True]).drop_duplicates('session_date').sort_values('session_date')
  if len(q)<60: continue
  for h in H:
   raw=q[f'fwd_{h}'].to_numpy(float); ok=np.isfinite(raw); raw=raw[ok]; yrs=pd.to_datetime(q.loc[ok,'session_date']).dt.year.to_numpy()
   if len(raw)==0: continue
   for c in COSTS:
    z=raw-2*c/10000; y=[float(z[yrs==y].sum()) for y in sorted(set(yrs))]
    rows.append(dict(gap_bps=g,horizon_min=h,cost_bps_side=c,events=len(z),trades_per_week=len(z)/339,simple_pnl=float(z.sum()),mean_return=float(z.mean()),win_rate=float((z>0).mean()),max_drawdown=dd(z),positive_years=sum(v>0 for v in y),years_tested=len(y),worst_year_simple=min(y)))
 pd.DataFrame(rows).to_csv(out/'gap_forward_stats.csv',index=False)
 (out/'run_metadata.json').write_text(json.dumps({'symbols':SYMS,'events':len(df),'horizons':list(H),'costs_bps_side':list(COSTS),'signal':'completed first 5m bar; next bar open entry; previous session close gap; 2026 sealed','cpu_threads':16},indent=2),encoding='utf-8')
 print(json.dumps({'events':len(df),'rows':len(rows)},indent=2))
if __name__=='__main__': main()
