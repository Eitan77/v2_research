from __future__ import annotations
import argparse,json,time
from pathlib import Path
import duckdb,numpy as np,pandas as pd,torch

SYMS=('SPY','QQQ','IWM','DIA','TQQQ','SQQQ','SOXL','SOXS','SMH','XLK','XLF','XLE','TLT','GLD','USO','ARKK','VOO','IVV','VIXY');H=tuple(range(5,61,5));TH=(0,10,25,50,100);RV=(0,1,1.5,2);COST=(0,2,5,10,25)
def dd(x):
 e=np.cumsum(x);return float(np.min(e-np.maximum.accumulate(e))) if len(e) else np.nan
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--catalog',default='D:/AlgoResearch/data/catalog.duckdb');ap.add_argument('--out',required=True);a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True);marks=','.join('?' for _ in SYMS)
 hs=','.join(f"max(case when f.m=e.entry_m+{h} then f.close/e.entry_open-1 end) as fwd_{h}" for h in H)
 sql=f'''with b as (select symbol,session_date,cast(timestamp as timestamptz) at time zone 'America/New_York' et,open,high,low,close,relative_volume_20 rvol from research_matrix where timeframe='5m' and session_date between '2019-06-21' and '2025-12-31' and symbol in ({marks})),x as (select *,extract(hour from et)*60+extract(minute from et) m from b),z as (select x.*,max(high) over(partition by symbol,session_date order by m rows between 20 preceding and 1 preceding) prior_hi from x),e as (select s.symbol,s.session_date,s.m,s.close/s.prior_hi-1 breakout,s.rvol,e.m entry_m,e.open entry_open from z s join x e using(symbol,session_date) where s.m between 670 and 945 and e.m=s.m+5 and s.prior_hi>0 and s.close>s.prior_hi and e.open>0),p as (select e.symbol,e.session_date,e.m,e.breakout,e.rvol,e.entry_m,e.entry_open,{hs} from e join x f on f.symbol=e.symbol and f.session_date=e.session_date and f.m between e.entry_m+5 and 955 group by e.symbol,e.session_date,e.m,e.breakout,e.rvol,e.entry_m,e.entry_open) select * from p'''
 c=duckdb.connect(a.catalog,read_only=True);c.execute('set threads=16');c.execute("set temp_directory='D:/AlgoResearch/work/duck_tmp'")
 try:d=c.execute(sql,list(SYMS)).fetchdf()
 finally:c.close()
 d.to_parquet(out/'breakout_event_paths.parquet',index=False);rows=[];dev=torch.device('cuda' if torch.cuda.is_available() else 'cpu');P=torch.as_tensor(np.nan_to_num(d[[f'fwd_{h}' for h in H]].to_numpy(np.float32)),device=dev);V=torch.as_tensor(np.isfinite(d[[f'fwd_{h}' for h in H]].to_numpy(np.float32)),device=dev)
 for th in TH:
  for rvol in RV:
   q=d[(d.breakout>=th/10000)&(d.rvol>=rvol)].copy();q['score']=q.breakout;q=q.sort_values(['session_date','m','score','symbol'],ascending=[True,True,False,True]).drop_duplicates(['session_date','m']).sort_values(['session_date','m']);
   if len(q)<100:continue
   idx=q.index.to_numpy();x=P[idx];v=V[idx];yrs=pd.to_datetime(q.session_date).dt.year.to_numpy()
   for hi,h in enumerate(H):
    z=x[:,hi][v[:,hi]]
    if z.numel()<100:continue
    ok=v[:,hi].detach().cpu().numpy().astype(bool); yy=yrs[ok]
    for cost in COST:
     net=z-2*cost/10000;ys=[float(net[yy==y].sum().item()) for y in sorted(set(yy))];rows.append(dict(threshold_bps=th,min_rvol=rvol,horizon_min=h,cost_bps_side=cost,events=int(net.numel()),trades_per_week=int(net.numel())/339,simple_pnl=float(net.sum().item()),mean_return=float(net.mean().item()),win_rate=float((net>0).float().mean().item()),max_drawdown=dd(net.detach().cpu().numpy()),positive_years=sum(vv>0 for vv in ys),years_tested=len(ys),worst_year_simple=min(ys)))
 pd.DataFrame(rows).to_csv(out/'breakout_stats.csv',index=False);(out/'run_metadata.json').write_text(json.dumps({'device':str(dev),'event_rows':len(d),'signal':'shifted prior 20-bar high breakout; next-bar entry; strongest signal per session/timestamp','symbols':SYMS,'horizons':list(H)},indent=2),encoding='utf-8');print(json.dumps({'device':str(dev),'events':len(d),'rows':len(rows)},indent=2))
if __name__=='__main__':main()
