from __future__ import annotations
import argparse, json, time
from pathlib import Path
import duckdb, numpy as np, pandas as pd, torch

SYMS=('TQQQ','SQQQ','SOXL','SOXS'); H=tuple(range(5,61,5)); RANK=(0.25,0.5,1.0); MOVE=(0,25,50,100,200,400); TP=(10,25,50,100,200); SL=(5,10,25,50,100); COST=(0,2,5,10)
def dd(x):
 e=np.cumsum(x); return float(np.min(e-np.maximum.accumulate(e))) if len(e) else np.nan
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--catalog',default='D:/AlgoResearch/data/catalog.duckdb'); ap.add_argument('--out',required=True); a=ap.parse_args(); out=Path(a.out);out.mkdir(parents=True,exist_ok=True); marks=','.join('?' for _ in SYMS)
 hs=','.join(f"max(case when f.m=905+{h} then f.close/r.entry_open-1 end) as cl_{h},max(case when f.m between 910 and 905+{h} then f.high/r.entry_open-1 end) as hi_{h},min(case when f.m between 910 and 905+{h} then f.low/r.entry_open-1 end) as lo_{h}" for h in H)
 sql=f'''with b as (select symbol,session_date,cast(timestamp as timestamptz) at time zone 'America/New_York' et,open,high,low,close from research_matrix where timeframe='5m' and session_date between '2019-06-21' and '2025-12-31' and symbol in ({marks})),x as (select *,extract(hour from et)*60+extract(minute from et) m from b),op as (select symbol,session_date,max(open) filter(where m=570) session_open from x group by 1,2),s as (select x.symbol,x.session_date,x.close/op.session_open-1 move_ret,e.open entry_open from x join op using(symbol,session_date) join x e using(symbol,session_date) where x.m=900 and e.m=905 and op.session_open>0 and e.open>0),r as (select *,percent_rank() over(partition by session_date order by move_ret) rank_pct from s),p as (select r.symbol,r.session_date,r.move_ret,r.rank_pct,r.entry_open,{hs} from r join x f using(symbol,session_date) where r.rank_pct>=0.75 and f.m between 910 and 955 group by r.symbol,r.session_date,r.move_ret,r.rank_pct,r.entry_open) select * from p order by session_date,symbol'''
 c=duckdb.connect(a.catalog,read_only=True);c.execute('set threads=16');c.execute("set temp_directory='D:/AlgoResearch/work/duck_tmp'")
 try:d=c.execute(sql,list(SYMS)).fetchdf()
 finally:c.close()
 d.to_parquet(out/'late_momentum_event_paths.parquet',index=False); picks=[];keys=[]
 for rl in RANK:
  for mb in MOVE:
   q=d[(d.rank_pct>=1-rl)&(d.move_ret>=mb/10000)].copy();q['score']=q.move_ret;q=q.sort_values(['session_date','score'],ascending=[True,False]).drop_duplicates('session_date').sort_values('session_date');
   if len(q)<60:continue
   picks.append(q);keys.append((rl,mb))
 maxn=max(len(q) for q in picks); hi=np.full((len(picks),maxn,len(H)),np.nan,np.float32);lo=hi.copy();cl=hi.copy();yrs=[]
 for i,q in enumerate(picks):
  hi[i,:len(q)]=q[[f'hi_{h}' for h in H]].to_numpy(np.float32);lo[i,:len(q)]=q[[f'lo_{h}' for h in H]].to_numpy(np.float32);cl[i,:len(q)]=q[[f'cl_{h}' for h in H]].to_numpy(np.float32);yrs.append(pd.to_datetime(q.session_date).dt.year.to_numpy())
 dev=torch.device('cuda' if torch.cuda.is_available() else 'cpu');thi=torch.as_tensor(np.nan_to_num(hi),device=dev);tlo=torch.as_tensor(np.nan_to_num(lo),device=dev);tcl=torch.as_tensor(np.nan_to_num(cl),device=dev);valid=torch.as_tensor(np.isfinite(cl),device=dev)
 tpv=torch.as_tensor(np.array(TP,np.float32)/10000,device=dev).reshape(1,1,len(TP),1,1);slv=torch.as_tensor(np.array(SL,np.float32)/10000,device=dev).reshape(1,1,1,len(SL),1);cv=torch.as_tensor(2*np.array(COST,np.float32)/10000,device=dev).reshape(1,1,1,1,len(COST));rows=[]
 for i,key in enumerate(keys):
  xh=thi[i].unsqueeze(2).unsqueeze(3).unsqueeze(4);xl=tlo[i].unsqueeze(2).unsqueeze(3).unsqueeze(4);xc=tcl[i].unsqueeze(2).unsqueeze(3).unsqueeze(4);vv=valid[i].unsqueeze(2).unsqueeze(3).unsqueeze(4);g=torch.where(xl.le(-slv),-slv,torch.where(xh.ge(tpv),tpv,xc))-cv;g=torch.where(vv,g,torch.full_like(g,float('nan')))
  for hi_i,h in enumerate(H):
   for ti,tp in enumerate(TP):
    for si,sl in enumerate(SL):
     for ci,cost in enumerate(COST):
      z=g[:,hi_i,ti,si,ci].detach().cpu().numpy()[:len(picks[i])];ok=np.isfinite(z);z=z[ok];y=yrs[i][ok]
      if len(z)<60:continue
      ys=[float(z[y==yy].sum()) for yy in sorted(set(y))];rows.append(dict(rank_level=key[0],move_bps=key[1],horizon_min=h,tp_bps=tp,sl_bps=sl,cost_bps_side=cost,events=len(z),trades_per_week=len(z)/339,simple_pnl=float(z.sum()),mean_return=float(z.mean()),win_rate=float((z>0).mean()),max_drawdown=dd(z),positive_years=sum(v>0 for v in ys),years_tested=len(ys),worst_year_simple=min(ys)))
 pd.DataFrame(rows).to_csv(out/'late_momentum_tp_sl_grid.csv',index=False);(out/'run_metadata.json').write_text(json.dumps({'device':str(dev),'event_rows':len(d),'filters':len(keys),'symbols':SYMS,'signal':'15:00 completed cross-sectional move, 15:05 entry, exit horizons through close','gpu_scan':'full path TP/SL/cost tensor'},indent=2),encoding='utf-8');print(json.dumps({'device':str(dev),'events':len(d),'rows':len(rows)},indent=2))
if __name__=='__main__':main()
