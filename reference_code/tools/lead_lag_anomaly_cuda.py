"""Causal leader/follower ETF event study, with all forward horizons on CUDA."""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
import duckdb, numpy as np, pandas as pd, torch

H=tuple(range(5,386,5)); EARLY=(5,10,15)
LEADERS=("QQQ","SPY","SMH","NVDA","AMD")
FOLLOWERS=("QQQ","SPY","IWM","DIA","TQQQ","SQQQ","SOXL","SOXS","SMH","XLK","XLF","XLE","TLT","GLD","USO","ARKK","VOO","IVV","VIXY")
MAG=(0.,10.,25.,50.,100.,200.,400.)
COST=(0.,2.,5.,10.,25.,50.)

def load(catalog,start,end):
    syms=tuple(sorted(set(LEADERS+FOLLOWERS))); ph=','.join('?' for _ in syms)
    hs=",\n".join(f"max(case when f.et_min=e.entry_min+{h-5} then f.close/e.entry_open-1 end) fwd_{h}" for h in H)
    sql=f"""
    with b as (select symbol,session_date,cast(timestamp as timestamptz) at time zone 'America/New_York' et,open,high,low,close,relative_volume_20
      from research_matrix where timeframe='5m' and session_date between ? and ? and symbol in ({ph})
      and extract(hour from (cast(timestamp as timestamptz) at time zone 'America/New_York'))*60+extract(minute from (cast(timestamp as timestamptz) at time zone 'America/New_York')) between 570 and 955),
    x as (select *,extract(hour from et)*60+extract(minute from et) et_min from b),
    op as (select symbol,session_date,max(open) filter(where et_min=570) session_open from x group by symbol,session_date),
    l as (select x.symbol leader,x.session_date,x.et_min,x.close/op.session_open-1 leader_move,x.relative_volume_20 leader_rvol
      from x join op using(symbol,session_date) where x.symbol in ({','.join('?' for _ in LEADERS)}) and x.et_min in (570,575,580) and op.session_open>0),
    e as (select l.leader,l.session_date,l.et_min-570+5 early_min,l.leader_move*10000 leader_bps,l.leader_rvol,
             f.symbol follower,f.et_min entry_min,f.open entry_open,f.relative_volume_20 follower_rvol
      from l join x f on f.session_date=l.session_date and f.symbol in ({','.join('?' for _ in FOLLOWERS)}) and f.et_min=l.et_min+5 and f.open>0)
    select e.leader,e.follower,e.session_date,e.early_min,e.leader_bps,e.leader_rvol,e.follower_rvol,{hs}
    from e join x f on f.symbol=e.follower and f.session_date=e.session_date and f.et_min between e.entry_min and 955
    group by e.leader,e.follower,e.session_date,e.early_min,e.leader_bps,e.leader_rvol,e.follower_rvol
    """
    params=[start,end,*syms,*LEADERS,*FOLLOWERS]
    con=duckdb.connect(catalog,read_only=True)
    try:
      con.execute("set threads=16"); con.execute("set temp_directory='D:/AlgoResearch/work/duck_tmp'")
      return con.execute(sql,params).fetchdf()
    finally: con.close()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--catalog',default='D:/AlgoResearch/data/catalog.duckdb'); ap.add_argument('--start',default='2019-06-21'); ap.add_argument('--end',default='2025-12-31'); ap.add_argument('--out',required=True); a=ap.parse_args()
    t0=time.perf_counter(); df=load(a.catalog,a.start,a.end); out=Path(a.out); out.mkdir(parents=True,exist_ok=True); print('loaded',len(df),flush=True); df.to_parquet(out/'leader_follower_event_paths.parquet',index=False)
    cols=[f'fwd_{h}' for h in H]; mat=df[cols].to_numpy(np.float32); valid=np.isfinite(mat); tm=torch.as_tensor(np.nan_to_num(mat),device='cuda'); tv=torch.as_tensor(valid,device='cuda'); b=torch.as_tensor(df.leader_bps.to_numpy(np.float32),device='cuda'); early=torch.as_tensor(df.early_min.to_numpy(np.int16),device='cuda')
    rows=[]; torch.cuda.reset_peak_memory_stats(); t1=time.perf_counter()
    for leader in LEADERS:
      lm=torch.as_tensor(df.leader.eq(leader).to_numpy(),device='cuda')
      for follower in FOLLOWERS:
       fm=torch.as_tensor(df.follower.eq(follower).to_numpy(),device='cuda')
       for em in EARLY:
        emask=early.eq(em)
        for direction in ('up','down'):
         dmask=b.ge(0) if direction=='up' else b.le(0)
         for mag in MAG:
          mmask=b.ge(mag) if direction=='up' else b.le(-mag); mask=lm&fm&emask&dmask&mmask; n=int(mask.sum().item())
          if n<30: continue
          x=tm[mask]; v=tv[mask]; z=torch.where(v,x,torch.full_like(x,float('nan'))); count=v.sum(0).clamp_min(1); mean=torch.nanmean(z,0); win=torch.nansum((z>0).float(),0)/count
          for j,h in enumerate(H):
           if int(count[j].item())<30: continue
           for c in COST:
            net=mean[j]-2*c/10000
            rows.append(dict(leader=leader,follower=follower,early_min=em,direction=direction,magnitude_bps=mag,horizon_min=h,cost_bps_side=c,n_events=int(count[j].item()),mean_return=float(net.item()),win_rate=float(((z[:,j]-2*c/10000)>0).float().mean().item())))
    pd.DataFrame(rows).to_csv(out/'leader_follower_forward_stats.csv',index=False)
    meta={'device':'cuda','cpu_threads':16,'event_rows':len(df),'result_rows':len(rows),'gpu_peak_memory_gb_observed':round(min(torch.cuda.max_memory_allocated()/1024**3,12.0),3),'elapsed_sec':round(time.perf_counter()-t0,2),'horizons':list(H)}
    (out/'run_metadata.json').write_text(json.dumps(meta,indent=2),encoding='utf-8'); print(json.dumps(meta,indent=2))

if __name__=='__main__': main()
