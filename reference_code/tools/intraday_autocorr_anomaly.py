from __future__ import annotations
import argparse,json,time
from pathlib import Path
import duckdb,numpy as np,pandas as pd,torch

SYMS=('SPY','QQQ','IWM','DIA','TQQQ','SQQQ','SOXL','SOXS','SMH','XLK','XLF','XLE','TLT','GLD','USO','ARKK','VOO','IVV','VIXY')
H=tuple(range(5,121,5)); MAG=(0,5,10,25,50,100,200,400); COST=(0,2,5,10,25)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--catalog',default='D:/AlgoResearch/data/catalog.duckdb');ap.add_argument('--out',required=True);a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True);marks=','.join('?' for _ in SYMS)
 hs=','.join(f"max(case when f.m=e.entry_m+{h} then f.close/e.entry_open-1 end) as fwd_{h}" for h in H)
 sql=f'''with b as (select symbol,session_date,cast(timestamp as timestamptz) at time zone 'America/New_York' et,open,close from research_matrix where timeframe='5m' and session_date between '2019-06-21' and '2025-12-31' and symbol in ({marks})),x as (select *,extract(hour from et)*60+extract(minute from et) m from b),s as (select x.*,x.close/x.open-1 signal_ret,lead(x.m) over(partition by x.symbol,x.session_date order by x.m) next_m from x),e as (select s.symbol,s.session_date,s.m,s.signal_ret,e.m entry_m,e.open entry_open from s join x e on e.symbol=s.symbol and e.session_date=s.session_date and e.m=s.m+5 where s.m between 570 and 945 and s.open>0 and s.close>0 and e.open>0),p as (select e.symbol,e.session_date,e.m,e.signal_ret,e.entry_m,e.entry_open,{hs} from e join x f on f.symbol=e.symbol and f.session_date=e.session_date and f.m between e.entry_m+5 and 955 group by e.symbol,e.session_date,e.m,e.signal_ret,e.entry_m,e.entry_open) select * from p'''
 c=duckdb.connect(a.catalog,read_only=True);c.execute('set threads=16');c.execute("set temp_directory='D:/AlgoResearch/work/duck_tmp'")
 try:d=c.execute(sql,list(SYMS)).fetchdf()
 finally:c.close()
 d.to_parquet(out/'intraday_autocorr_event_paths.parquet',index=False);rows=[];t0=time.perf_counter();dev=torch.device('cuda' if torch.cuda.is_available() else 'cpu');rets=torch.as_tensor(d.signal_ret.to_numpy(np.float32),device=dev);paths=torch.as_tensor(np.nan_to_num(d[[f'fwd_{h}' for h in H]].to_numpy(np.float32)),device=dev);valid=torch.as_tensor(np.isfinite(d[[f'fwd_{h}' for h in H]].to_numpy(np.float32)),device=dev)
 for direction in ('up','down'):
  for mag in MAG:
   mask=(rets>=mag/10000) if direction=='up' else (rets<=-mag/10000)
   if direction=='up':mask=mask & (rets>=0)
   else:mask=mask & (rets<=0)
   if int(mask.sum())<100:continue
   x=paths[mask];v=valid[mask]
   for hi,h in enumerate(H):
    z=x[:,hi][v[:,hi]]
    if z.numel()<100:continue
    for cost in COST:
     q=z-2*cost/10000;rows.append(dict(direction=direction,magnitude_bps=mag,horizon_min=h,cost_bps_side=cost,n_events=int(q.numel()),mean_return=float(q.mean().item()),median_return=float(q.median().item()),win_rate=float((q>0).float().mean().item()),p10=float(torch.quantile(q,.1).item()),p90=float(torch.quantile(q,.9).item())))
 pd.DataFrame(rows).to_csv(out/'intraday_autocorr_stats.csv',index=False);(out/'run_metadata.json').write_text(json.dumps({'symbols':SYMS,'event_rows':len(d),'horizons':list(H),'device':str(dev),'cuda_elapsed_sec':time.perf_counter()-t0,'signal':'completed 5m bar return, next-bar entry, horizons through 120m','costs_bps_side':COST},indent=2),encoding='utf-8');print(json.dumps({'events':len(d),'rows':len(rows),'device':str(dev)},indent=2))
if __name__=='__main__':main()
