from __future__ import annotations
import argparse, json
from pathlib import Path
import duckdb, numpy as np, pandas as pd

LEADERS=('QQQ','SPY'); FOLLOWERS={'up':'TQQQ','down':'SQQQ'}; SIGNALS=(15,30,60); THRESH=(0,10,25,50,100); COSTS=(0,2,5,10,25,50); H=(5,10,15,20,25)
def dd(x):
 e=np.cumsum(x); return float(np.min(e-np.maximum.accumulate(e))) if len(e) else np.nan
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--catalog',default='D:/AlgoResearch/data/catalog.duckdb'); ap.add_argument('--out',required=True); a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
 syms=LEADERS+tuple(FOLLOWERS.values()); marks=','.join('?' for _ in syms)
 hs=','.join(f"max(case when f.m=930+{h} then f.close/e.entry_open-1 end) as fwd_{h},max(case when f.m between 930 and 930+{h} then f.high/e.entry_open-1 end) as hi_{h},min(case when f.m between 930 and 930+{h} then f.low/e.entry_open-1 end) as lo_{h}" for h in H)
 sql=f'''with b as (select symbol,session_date,cast(timestamp as timestamptz) at time zone 'America/New_York' et,open,high,low,close from research_matrix where timeframe='5m' and session_date between '2019-06-21' and '2025-12-31' and symbol in ({marks})), x as (select *,extract(hour from et)*60+extract(minute from et) m from b), d0 as (select symbol,session_date,max(open) filter(where m=570) session_open,max(close) filter(where m=955) session_close from x group by 1,2), op as (select *,lag(session_close) over(partition by symbol order by session_date) prev_close from d0), leaders as (select x.symbol,x.session_date,x.m,x.close/op.prev_close-1 leader_ret from x join op using(symbol,session_date) where x.symbol in (?,?) and x.m in (870,900,925) and op.prev_close>0), entries as (select l.symbol leader,l.session_date,l.m,case when l.leader_ret>=0 then 'up' else 'down' end direction,l.leader_ret,f.symbol follower,f.open entry_open from leaders l join x f on f.session_date=l.session_date and f.symbol=case when l.leader_ret>=0 then 'TQQQ' else 'SQQQ' end and f.m=930 where f.open>0), paths as (select e.*,{hs} from entries e join x f on f.symbol=e.follower and f.session_date=e.session_date and f.m between 935 and 955 group by e.leader,e.session_date,e.m,e.direction,e.leader_ret,e.follower,e.entry_open) select * from paths'''
 con=duckdb.connect(a.catalog,read_only=True); con.execute('set threads=16'); con.execute("set temp_directory='D:/AlgoResearch/work/duck_tmp'")
 try: df=con.execute(sql,list(syms)+list(LEADERS)).fetchdf()
 finally: con.close()
 df.to_parquet(out/'close_hedging_event_paths.parquet',index=False); rows=[]
 for sig in SIGNALS:
  m=870 if sig==60 else (900 if sig==30 else 925)
  for leader in LEADERS:
   q0=df[(df.leader==leader)&(df.m==m)].copy()
   for t in THRESH:
    q=q0[abs(q0.leader_ret)>=t/10000].copy()
    if len(q)<30: continue
    for h in H:
     raw=q[f'fwd_{h}'].to_numpy(float); ok=np.isfinite(raw); raw=raw[ok]; yrs=pd.to_datetime(q.loc[ok,'session_date']).dt.year.to_numpy()
     if len(raw)<30: continue
     for c in COSTS:
      z=raw-2*c/10000; y=[float(z[yrs==y].sum()) for y in sorted(set(yrs))]
      rows.append(dict(leader=leader,signal_minutes_before_close=sig,threshold_bps=t,horizon_min=h,cost_bps_side=c,events=len(z),trades_per_week=len(z)/339,simple_pnl=float(z.sum()),mean_return=float(z.mean()),win_rate=float((z>0).mean()),max_drawdown=dd(z),positive_years=sum(v>0 for v in y),years_tested=len(y),worst_year_simple=min(y)))
 pd.DataFrame(rows).to_csv(out/'close_hedging_stats.csv',index=False); (out/'run_metadata.json').write_text(json.dumps({'leaders':LEADERS,'followers':FOLLOWERS,'signal':'completed leader return into 15:30/15:00/14:30; entry at 15:30; direction buys TQQQ or SQQQ; exit same day','events':len(df),'cpu_threads':16},indent=2),encoding='utf-8'); print(json.dumps({'events':len(df),'rows':len(rows)},indent=2))
if __name__=='__main__': main()
