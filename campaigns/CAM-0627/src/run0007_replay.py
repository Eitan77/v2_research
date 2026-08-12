from __future__ import annotations
import gzip,importlib.util,json
from concurrent.futures import ProcessPoolExecutor,as_completed
from itertools import product
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];CAM=ROOT/'campaigns'/'CAM-0627';R3=CAM/'artifacts'/'RUN-0003';OUT=CAM/'artifacts'/'RUN-0007';RAW3=R3/'raw_quotes';RAW7=OUT/'raw_quotes';PAIRS=(('SPY','VOO'),('IVV','VOO'),('QQQ','QQQM'));STEP_MS=50
spec=importlib.util.spec_from_file_location('r3',CAM/'src'/'run0003_sync_and_replay.py');r3=importlib.util.module_from_spec(spec);spec.loader.exec_module(r3)
def configs():return list(product((100,250),(30,60),(0,.25,.5,1,2,5,10),(1,5,30,120),(5,10,20)))
def load_rows(path):
 with gzip.open(path,'rt') as h:return json.load(h)
def sync_symbol(grid,rows,prefix):
 q=pd.DataFrame(rows,columns=['t','bp','ap','bs','as']);q['source_ts']=pd.to_datetime(q.pop('t'),utc=True,format='mixed').astype('datetime64[ns, UTC]');q=q.sort_values('source_ts').drop_duplicates('source_ts',keep='last');q=q[(q.bp>0)&(q.ap>=q.bp)].rename(columns={c:f'{prefix}_{c}' for c in ('bp','ap','bs','as')});z=pd.merge_asof(grid,q,left_on='ts',right_on='source_ts',direction='backward');z[f'{prefix}_age_ms']=(z.ts-z.source_ts).dt.total_seconds()*1000;return z.drop(columns='source_ts')
def process_window(job):
 date,label,start,end=job;dest=OUT/'window_results'/f'{date}_{label}.parquet'
 if dest.exists():return {'path':str(dest),'stats':[]}
 q3=load_rows(RAW3/f'{date}_{label}.json.gz')['quotes'];q7=load_rows(RAW7/f'{date}_{label}.json.gz')['quotes'];quotes={**q3,**q7};st=pd.Timestamp(f'{date} {start}',tz='America/New_York').tz_convert('UTC');en=pd.Timestamp(f'{date} {end}',tz='America/New_York').tz_convert('UTC');base=pd.DataFrame({'ts':pd.date_range(st,en,freq=f'{STEP_MS}ms',inclusive='left').astype('datetime64[ns, UTC]')});rows=[];stats=[]
 for sa,sb in PAIRS:
  x=sync_symbol(base,quotes[sa],'a');x=sync_symbol(x,quotes[sb],'b');x['date']=date;x['window']=label;x['mid_log_ratio']=np.log((x.a_bp+x.a_ap)/(x.b_bp+x.b_ap))
  for sec in (30,60):x[f'anchor_{sec}']=x.mid_log_ratio.shift(1).rolling(sec*1000//STEP_MS,min_periods=sec*1000//STEP_MS).median()
  va=(x.a_age_ms<=250)&(x.b_age_ms<=250)&x.anchor_30.notna();ea=(np.log(x.a_bp/x.b_ap)-x.anchor_30)[va]*1e4;eb=(x.anchor_30-np.log(x.a_ap/x.b_bp))[va]*1e4;stats.append({'date':date,'window':label,'pair':f'{sa}_{sb}','valid_snapshots':int(va.sum()),'max_executable_edge_bps':float(max(ea.max() if len(ea) else -np.inf,eb.max() if len(eb) else -np.inf))})
  for age,anchor,threshold,hold,stop in configs():
   tr=r3.simulate_window(x,age,anchor,threshold,hold,stop)
   for r in tr:r.update({'pair':f'{sa}_{sb}','age_ms':age,'anchor_seconds':anchor,'threshold_bps':threshold,'hold_seconds':hold,'stop_bps':stop})
   rows.extend(tr)
 dest.parent.mkdir(parents=True,exist_ok=True);pd.DataFrame(rows).to_parquet(dest,index=False);return {'path':str(dest),'stats':stats}
def met(t,b):
 if t.empty:return {'trades':0,'net_return':0.,'average_net_trade_bps':0.,'win_rate':0.,'positive_windows':0,'negative_windows':0,'positive_months':0,'negative_months':0,'worst_window':0.}
 t=t.copy();t['net']=t.gross_pnl-2*b/10000;w=t.groupby(['date','window']).net.sum();m=t.groupby('date').net.sum();return {'trades':len(t),'net_return':float(t.net.sum()),'average_net_trade_bps':float(t.net.mean()*1e4),'win_rate':float((t.net>0).mean()),'positive_windows':int((w>0).sum()),'negative_windows':int((w<0).sum()),'positive_months':int((m>0).sum()),'negative_months':int((m<0).sum()),'worst_window':float(w.min())}
def main():
 OUT.mkdir(parents=True,exist_ok=True);m=pd.read_parquet(R3/'manifest.parquet');jobs=[(r.date,r.label,r.start,r.end) for r in m.itertuples()];paths=[];stats=[]
 with ProcessPoolExecutor(max_workers=16) as pool:
  fs={pool.submit(process_window,j):j for j in jobs}
  for i,f in enumerate(as_completed(fs),1):z=f.result();paths.append(z['path']);stats.extend(z['stats']);print(f'completed_windows={i}/{len(jobs)}',flush=True) if i%6==0 else None
 frames=[pd.read_parquet(p) for p in paths];all_t=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame();all_t.to_parquet(OUT/'trades.parquet',index=False);pd.DataFrame(stats).to_parquet(OUT/'edge_stats.parquet',index=False);rows=[]
 for sa,sb in PAIRS:
  pair=f'{sa}_{sb}'
  for age,anchor,threshold,hold,stop in configs():
   mask=(all_t.pair.eq(pair)&all_t.age_ms.eq(age)&all_t.anchor_seconds.eq(anchor)&all_t.threshold_bps.eq(threshold)&all_t.hold_seconds.eq(hold)&all_t.stop_bps.eq(stop)) if len(all_t) else pd.Series([],dtype=bool);t=all_t[mask] if len(all_t) else all_t
   for b in (0,1,2):z=met(t,b);z.update({'pair':pair,'age_ms':age,'anchor_seconds':anchor,'threshold_bps':threshold,'hold_seconds':hold,'stop_bps':stop,'additional_bps_per_side':b});rows.append(z)
 grid=pd.DataFrame(rows);grid.to_parquet(OUT/'grid.parquet',index=False);best={}
 for b in (0,1,2):
  z=grid[grid.additional_bps_per_side.eq(b)].sort_values('net_return',ascending=False).iloc[0];best[str(b)]={k:(v.item() if hasattr(v,'item') else v) for k,v in z.items()}
 edge=pd.DataFrame(stats).groupby('pair').agg(max_executable_edge_bps=('max_executable_edge_bps','max'),valid_snapshots=('valid_snapshots','sum')).reset_index().to_dict('records');report={'status':'completed','planned_variants':1008,'executed_variants':int(len(grid)/3),'workers':16,'edge_summary':edge,'best_by_cost':best,'maximum_loaded_date':str(pd.to_datetime(m.date).max().date()),'holdout_rows_loaded':0};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n');print(json.dumps(report,indent=2,default=str))
if __name__=='__main__':main()
