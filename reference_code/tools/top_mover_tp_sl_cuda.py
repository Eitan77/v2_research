"""CUDA TP/SL grid for the causal top-opening-mover event family."""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd
import torch

H = tuple(range(5, 386, 5))
RANK_LEVELS = (0.005, 0.01, 0.02, 0.05, 0.10, 0.20)
MOVE_BPS = (0.0, 25.0, 50.0, 100.0, 200.0, 400.0, 800.0)
PRICE_FILTERS = (0.0, 5.0, 10.0, 20.0)
RVOL_FILTERS = (0.0, 1.0, 1.5, 2.0)
TP_BPS = (25.0, 50.0, 100.0, 200.0, 300.0, 500.0)
SL_BPS = (25.0, 50.0, 100.0, 200.0, 300.0)
COSTS = (0.0, 2.0, 5.0, 10.0, 25.0, 50.0)

def load(catalog: str, start: str, end: str, side: str, early_min: int) -> pd.DataFrame:
    hs = ",\n".join(f"max(case when f.et_min between e.entry_min and e.entry_min+{h-5} then f.high/e.entry_open-1 end) as hi_{h}, min(case when f.et_min between e.entry_min and e.entry_min+{h-5} then f.low/e.entry_open-1 end) as lo_{h}, max(case when f.et_min=e.entry_min+{h-5} then f.close/e.entry_open-1 end) as cl_{h}" for h in H)
    sql=f"""
    with b as (
      select symbol,session_date,cast(timestamp as timestamptz) at time zone 'America/New_York' as et,
             open,high,low,close,volume,relative_volume_20,atr_pct_14
      from research_matrix where timeframe='5m' and session_date between ? and ?
      and extract(hour from (cast(timestamp as timestamptz) at time zone 'America/New_York'))*60+extract(minute from (cast(timestamp as timestamptz) at time zone 'America/New_York')) between 570 and 955
    ), x as (
      select *,extract(hour from et)*60+extract(minute from et) as et_min from b
    ), op as (
      select symbol,session_date,max(open) filter(where et_min=570) session_open from x group by symbol,session_date
    ), e0 as (
      select s.symbol,s.session_date,s.close/sop.session_open-1 early_move,s.close signal_close,s.volume signal_volume,
             s.relative_volume_20 signal_rvol,s.atr_pct_14 signal_atr,e.et_min entry_min,e.open entry_open,e.volume entry_volume
      from x s join op sop using(symbol,session_date) join x e on e.symbol=s.symbol and e.session_date=s.session_date and e.et_min=s.et_min+5
    where s.et_min={570 + early_min - 5} and sop.session_open>0 and e.open>0
    ), e as (
      select *,percent_rank() over(partition by session_date order by early_move) rank_pct from e0
    )
    select e.symbol,e.session_date,e.early_move*10000 early_bps,e.rank_pct,e.signal_close,e.signal_volume,e.signal_rvol,e.signal_atr,e.entry_min,e.entry_open,e.entry_volume,
           {hs}
    from e join x f on f.symbol=e.symbol and f.session_date=e.session_date and f.et_min between e.entry_min and 955
    where {"e.rank_pct>=0.80 and e.early_move>=0" if side=='top' else "e.rank_pct<=0.20 and e.early_move<=0"}
    group by e.symbol,e.session_date,e.early_move,e.rank_pct,e.signal_close,e.signal_volume,e.signal_rvol,e.signal_atr,e.entry_min,e.entry_open,e.entry_volume
    order by e.session_date,e.symbol
    """
    con=duckdb.connect(catalog,read_only=True)
    try:
        con.execute("set threads=16"); con.execute("set temp_directory='D:/AlgoResearch/work/duck_tmp'")
        return con.execute(sql,[start,end]).fetchdf()
    finally: con.close()

def dd(x):
    e=np.cumsum(x); return float(np.min(e-np.maximum.accumulate(e))) if len(x) else np.nan

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--catalog',default='D:/AlgoResearch/data/catalog.duckdb'); ap.add_argument('--start',default='2019-06-21'); ap.add_argument('--end',default='2025-12-31'); ap.add_argument('--out',required=True); ap.add_argument('--side',choices=['top','bottom'],default='top'); ap.add_argument('--early-min',type=int,choices=[5,10,15],default=10); a=ap.parse_args()
    t0=time.perf_counter(); df=load(a.catalog,a.start,a.end,a.side,a.early_min); print('loaded',len(df),flush=True)
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True); stem=f'{a.side}_mover_{a.early_min}m'; df.to_parquet(out/f'{stem}_event_paths.parquet',index=False)
    years=pd.to_datetime(df.session_date).dt.year.to_numpy()
    # For each filter, choose exactly one eligible mover per day, preserving fixed capital.
    picks=[]; keys=[]
    for rl in RANK_LEVELS:
      for mb in MOVE_BPS:
       for pf in PRICE_FILTERS:
        for rv in RVOL_FILTERS:
         if a.side=='top':
          q=df[(df.rank_pct>=1-rl)&(df.early_bps>=mb)&(df.signal_close>=pf)&(df.signal_rvol>=rv)].copy()
         else:
          q=df[(df.rank_pct<=rl)&(df.early_bps<=-mb)&(df.signal_close>=pf)&(df.signal_rvol>=rv)].copy()
         if q.empty: continue
         q['score']=q.early_bps if a.side=='top' else -q.early_bps; q=q.sort_values(['session_date','score'],ascending=[True,False]).drop_duplicates('session_date')
         # Require enough observations for an honest cadence comparison.
         if len(q)<60: continue
         picks.append(q.index.to_numpy()); keys.append((rl,mb,pf,rv))
    if not picks: raise RuntimeError('no candidate event paths')
    maxn=max(map(len,picks)); n=len(picks)
    def pad_matrix(prefix):
      out=np.full((len(picks),maxn,len(H)),np.nan,dtype=np.float32)
      for i,idx in enumerate(picks):
        out[i,:len(idx),:]=df.loc[idx,[f'{prefix}_{h}' for h in H]].to_numpy(np.float32)
      return out
    hi=pad_matrix('hi'); lo=pad_matrix('lo'); cl=pad_matrix('cl')
    valid_np=np.isfinite(cl)
    dev=torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    if dev.type!='cuda': raise RuntimeError('CUDA required')
    torch.cuda.reset_peak_memory_stats(); thi=torch.as_tensor(np.nan_to_num(hi),device=dev); tlo=torch.as_tensor(np.nan_to_num(lo),device=dev); tcl=torch.as_tensor(np.nan_to_num(cl),device=dev); tvalid=torch.as_tensor(valid_np,device=dev)
    # Vectorize TP, SL, horizon, and cost dimensions together.  Batch size is
    # chosen from available VRAM, retaining headroom for cumsum/drawdown.
    free_bytes,total_bytes=torch.cuda.mem_get_info()
    per_candidate=maxn*len(H)*len(TP_BPS)*len(SL_BPS)*len(COSTS)*4
    batch_size=max(1,min(len(keys),int((total_bytes*0.70)//max(per_candidate*3,1))))
    tpv=torch.as_tensor(np.asarray(TP_BPS,np.float32)/10000,device=dev).reshape(1,1,1,len(TP_BPS),1,1)
    slv=torch.as_tensor(np.asarray(SL_BPS,np.float32)/10000,device=dev).reshape(1,1,1,1,len(SL_BPS),1)
    cv=torch.as_tensor(2*np.asarray(COSTS,np.float32)/10000,device=dev).reshape(1,1,1,1,1,len(COSTS))
    rows_path=out/f'{stem}_tp_sl_grid.csv'
    if rows_path.exists(): rows_path.unlink()
    wrote=False; t1=time.perf_counter(); total_grid_rows=0
    for lo_i in range(0,len(keys),batch_size):
      hi_i=min(lo_i+batch_size,len(keys))
      xhi=thi[lo_i:hi_i].unsqueeze(-1).unsqueeze(-1).unsqueeze(-1)
      xlo=tlo[lo_i:hi_i].unsqueeze(-1).unsqueeze(-1).unsqueeze(-1)
      xcl=tcl[lo_i:hi_i].unsqueeze(-1).unsqueeze(-1).unsqueeze(-1)
      valid=tvalid[lo_i:hi_i].unsqueeze(-1).unsqueeze(-1).unsqueeze(-1)
      hit_tp=xhi.ge(tpv); hit_sl=xlo.le(-slv)
      # Same-bar collision is pessimistic: stop wins.
      gross=torch.where(hit_sl,-slv,torch.where(hit_tp,tpv,xcl))-cv
      gross=torch.where(valid,gross,torch.full_like(gross,float('nan')))
      finite=torch.isfinite(gross); safe=torch.nan_to_num(gross,nan=0.0)
      count=finite.sum(dim=1); mean=safe.sum(dim=1)/count.clamp_min(1); win=((gross>0)&finite).sum(dim=1)/count.clamp_min(1)
      eq=torch.cumsum(safe,dim=1); runmax=torch.cummax(eq,dim=1).values; draw=(eq-runmax).amin(dim=1)
      arr=np.stack([mean.detach().cpu().numpy().ravel(),win.detach().cpu().numpy().ravel(),draw.detach().cpu().numpy().ravel(),count.detach().cpu().numpy().ravel()],axis=1)
      kk=[]
      for bi in range(hi_i-lo_i):
       for h in H:
        for tp in TP_BPS:
         for sl in SL_BPS:
          for cost in COSTS: kk.append((keys[lo_i+bi],h,tp,sl,cost))
      meta_rows=[]
      for (key,h,tp,sl,cost),(m,w,d,nv) in zip(kk,arr):
       if nv>=60: meta_rows.append(dict(rank_level=key[0],move_bps=key[1],min_price=key[2],min_rvol=key[3],tp_bps=tp,sl_bps=sl,horizon_min=h,cost_bps_side=cost,n_events=int(nv),simple_pnl=float(m*nv),mean_return=float(m),win_rate=float(w),max_drawdown=float(d)))
      pd.DataFrame(meta_rows).to_csv(rows_path,index=False,mode='a',header=not wrote); wrote=True; total_grid_rows+=len(meta_rows)
      print(f'cuda_batch={hi_i//batch_size}/{(len(keys)+batch_size-1)//batch_size} candidates={hi_i}/{len(keys)} batch_size={batch_size} peak_gb={torch.cuda.max_memory_allocated()/1024**3:.2f}',flush=True)
      del gross,safe,eq,runmax,draw
    meta={'device':str(dev),'cpu_threads':16,'candidate_filters':len(keys),'event_rows':len(df),'grid_rows':total_grid_rows,'gpu_batch_size':batch_size,'gpu_peak_memory_gb':round(torch.cuda.max_memory_allocated()/1024**3,3),'gpu_total_memory_gb':round(total_bytes/1024**3,3),'cuda_elapsed_sec':round(time.perf_counter()-t1,2),'total_elapsed_sec':round(time.perf_counter()-t0,2),'batch_policy':'VRAM-autotuned vectorized TP/SL/horizon/cost tensor; no data truncation'}
    meta['side']=a.side; meta['early_min']=a.early_min
    (out/f'{stem}_run_metadata.json').write_text(json.dumps(meta,indent=2),encoding='utf-8'); print(json.dumps(meta,indent=2))

if __name__=='__main__': main()
