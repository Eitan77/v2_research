from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np, pandas as pd

H=tuple(range(5,386,5)); LEADERS=("QQQ","SPY","SMH","NVDA","AMD"); FOLLOWERS=("SOXL","TQQQ","ARKK","VIXY"); COSTS=(0,2,5,10,25,50); MAG=(0,10,25,50,100,200)

def dd(x):
 e=np.cumsum(x); return float(np.min(e-np.maximum.accumulate(e))) if len(e) else np.nan

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); ap.add_argument('--out',required=True); a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
 d=pd.read_parquet(a.input); d['date']=pd.to_datetime(d.session_date); d['year']=d.date.dt.year
 rows=[]
 for follower in FOLLOWERS:
  for em in (5,10,15):
   for mag in MAG:
    for rl in [("all",LEADERS),("index",("QQQ","SPY")),("sector",("SMH","NVDA","AMD"))]:
     q=d[d.follower.eq(follower)&d.early_min.eq(em)&d.leader.isin(rl[1])&d.leader_bps.ge(mag)].copy()
     if q.empty: continue
     # strongest causal leader event wins; tie break by a stable leader name.
     q=q.sort_values(['session_date','leader_bps','leader'],ascending=[True,False,True]).drop_duplicates('session_date')
     if len(q)<60: continue
     for h in H:
      raw=q[f'fwd_{h}'].to_numpy(float); ok=np.isfinite(raw); raw=raw[ok]; years=q.loc[ok,'year'].to_numpy()
      if len(raw)<60: continue
      for c in COSTS:
       z=raw-2*c/10000
       ys=sorted(set(years)); yvals=[float(z[years==y].sum()) for y in ys]
       rows.append(dict(follower=follower,leader_set=rl[0],early_min=em,magnitude_bps=mag,horizon_min=h,cost_bps_side=c,events=len(z),trades_per_week=len(z)/339,simple_pnl=float(z.sum()),mean_return=float(z.mean()),win_rate=float((z>0).mean()),max_drawdown=dd(z),positive_years=sum(v>0 for v in yvals),years_tested=len(ys),worst_year_simple=min(yvals)))
 pd.DataFrame(rows).to_csv(out/'lead_lag_portfolio_screen.csv',index=False)
 print(json.dumps({'rows':len(rows),'max_events':int(max([r['events'] for r in rows],default=0))},indent=2))

if __name__=='__main__': main()
