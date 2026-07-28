from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np, pandas as pd

H=tuple(range(5,386,5)); COSTS=(0,2,5,10,25,50); MAG=(0,10,25,50,100,200)
RULES={
 'reversal_index':(('QQQ','SPY'),'down','TQQQ'),
 'reversal_sector':(('SMH','NVDA','AMD'),'down','SOXL'),
 'momentum_index':(('QQQ','SPY'),'up','TQQQ'),
 'momentum_sector':(('SMH','NVDA','AMD'),'up','SOXL'),
}
def dd(x):
 e=np.cumsum(x); return float(np.min(e-np.maximum.accumulate(e))) if len(e) else np.nan
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); ap.add_argument('--out',required=True); a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
 d=pd.read_parquet(a.input); d['date']=pd.to_datetime(d.session_date); d['year']=d.date.dt.year; rows=[]
 for name,(leaders,direction,follower) in RULES.items():
  for em in (5,10,15):
   for mag in MAG:
    for c in COSTS:
     q=d[d.leader.isin(leaders)&d.early_min.eq(em)&d.follower.eq(follower)].copy()
     if direction=='up': q=q[q.leader_bps>=mag]; q['score']=q.leader_bps
     elif direction=='down': q=q[q.leader_bps<=-mag]; q['score']=-q.leader_bps
     else:
      up=q[q.leader_bps>=mag].copy(); up['score']=up.leader_bps
      dn=q[q.leader_bps<=-mag].copy(); dn['score']=-dn.leader_bps; q=pd.concat([up,dn])
     if q.empty: continue
     q=q.sort_values(['session_date','score','leader'],ascending=[True,False,True]).drop_duplicates('session_date').sort_values('session_date')
     if len(q)<60: continue
     for h in H:
      raw=q[f'fwd_{h}'].to_numpy(float); ok=np.isfinite(raw); raw=raw[ok]; yrs=q.loc[ok,'year'].to_numpy()
      if len(raw)<60: continue
      z=raw-2*c/10000; ys=sorted(set(yrs)); yvals=[float(z[yrs==y].sum()) for y in ys]
      rows.append(dict(rule=name,early_min=em,magnitude_bps=mag,horizon_min=h,cost_bps_side=c,events=len(z),trades_per_week=len(z)/339,simple_pnl=float(z.sum()),mean_return=float(z.mean()),win_rate=float((z>0).mean()),max_drawdown=dd(z),positive_years=sum(v>0 for v in yvals),years_tested=len(ys),worst_year_simple=min(yvals)))
 pd.DataFrame(rows).to_csv(out/'lead_lag_combined_portfolio.csv',index=False); print(json.dumps({'rows':len(rows)},indent=2))
if __name__=='__main__': main()
