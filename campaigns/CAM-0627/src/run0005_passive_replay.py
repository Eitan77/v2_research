from __future__ import annotations
import gzip,json,os
from concurrent.futures import ProcessPoolExecutor,as_completed
from itertools import product
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];CAM=ROOT/'campaigns'/'CAM-0627';R3=CAM/'artifacts'/'RUN-0003';OUT=CAM/'artifacts'/os.environ.get('RUN_ID','RUN-0005');TR_SOURCE=CAM/'artifacts'/'RUN-0005';QR=R3/'raw_quotes';TR=TR_SOURCE/'raw_trades';STEP_NS=50_000_000;QSIZE_MULT=int(os.environ.get('QUOTE_SIZE_MULTIPLIER','100'))
def ns(v):return pd.to_datetime(v,utc=True,format='mixed').value
def passive_fill(qpack,tpack,side,order_ns,limit,queue_initial,target,lifetime_ms):
 q,qts=qpack;t,tts=tpack;deadline=order_ns+int(lifetime_ms*1e6);qi=np.searchsorted(qts,order_ns,'right');ti=np.searchsorted(tts,order_ns,'left');price_i,size_i=(1,2) if side=='buy' else (3,4);queue=max(0.,queue_initial);cur_price=limit;cur_ts=order_ns;lost=False;filled=0.
 while qi<len(q) or ti<len(t):
  nq=q[qi] if qi<len(q) else None;nt=t[ti] if ti<len(t) else None
  if nq is not None and nq[0]<=deadline and (nt is None or nq[0]<=nt[0]):
   qi+=1;p=nq[price_i];sz=nq[size_i];cur_ts=nq[0]
   if abs(p-limit)>1e-8:lost=True;cur_price=p
   else:
    if lost:queue=max(queue,sz);lost=False
    cur_price=p
   continue
  if nt is None or nt[0]>deadline:break
  ti+=1
  if lost or abs(cur_price-limit)>1e-8 or abs(nt[1]-limit)>1e-8 or nt[0]-cur_ts>250_000_000:continue
  consumed=min(queue,nt[2]);queue-=consumed;available=max(0.,nt[2]-consumed);filled+=min(target-filled,available)
  if filled>=target-1e-9:return nt[0]
 return None
def raw_window(date,label):
 with gzip.open(QR/f'{date}_{label}.json.gz','rt') as h:q=json.load(h)['quotes']
 with gzip.open(TR/f'{date}_{label}.json.gz','rt') as h:t=json.load(h)['trades']
 qp={};tp={}
 for s in ('SPY','IVV'):
  z=sorted([(ns(v['t']),float(v['bp']),float(v.get('bs',0))*QSIZE_MULT,float(v['ap']),float(v.get('as',0))*QSIZE_MULT) for v in q[s]],key=lambda x:x[0]);qp[s]=(z,np.array([v[0] for v in z],dtype=np.int64))
  z=sorted([(ns(v['t']),float(v['p']),float(v['s'])) for v in t[s]],key=lambda x:x[0]);tp[s]=(z,np.array([v[0] for v in z],dtype=np.int64))
 return qp,tp
def simulate(x,rawq,rawt,age,anchor_sec,threshold,lifetime,hold,passive_kind):
 anchor=x[f'anchor_{anchor_sec}'].to_numpy();abp=x.a_bp.to_numpy();aap=x.a_ap.to_numpy();bbp=x.b_bp.to_numpy();bap=x.b_ap.to_numpy();valid=(x.a_age_ms.to_numpy()<=age)&(x.b_age_ms.to_numpy()<=age)&np.isfinite(anchor);ea=np.log(abp/bap)-anchor;eb=anchor-np.log(aap/bbp);cand=valid&((ea>=threshold/10000)|(eb>=threshold/10000));ts=x.ts.astype('int64').to_numpy();n=len(x);rows=[];i=0
 while i<n-1:
  hits=np.flatnonzero(cand[i:n-1])
  if not len(hits):break
  e=i+int(hits[0]);rich_a=ea[e]>=eb[e];passive_symbol=('SPY' if rich_a else 'IVV') if passive_kind=='rich' else ('IVV' if rich_a else 'SPY');side='sell' if passive_kind=='rich' else 'buy';prefix='a' if passive_symbol=='SPY' else 'b';limit=(aap[e] if prefix=='a' else bap[e]) if side=='sell' else (abp[e] if prefix=='a' else bbp[e]);size=(x.a_as.iloc[e] if prefix=='a' else x.b_as.iloc[e]) if side=='sell' else (x.a_bs.iloc[e] if prefix=='a' else x.b_bs.iloc[e]);target=5000/limit;fill_ns=passive_fill(rawq[passive_symbol],rawt[passive_symbol],side,int(ts[e]),float(limit),float(size)*QSIZE_MULT,target,lifetime)
  if fill_ns is None:i=min(n,e+max(1,int(lifetime*1e6//STEP_NS))+1);continue
  hedge=int(np.searchsorted(ts,fill_ns,'left'))
  while hedge<n and not valid[hedge]:hedge+=1
  if hedge>=n-1:break
  if rich_a:
   es=limit if passive_kind=='rich' else abp[hedge];el=bap[hedge] if passive_kind=='rich' else limit;short_passive=passive_kind=='rich';long_passive=passive_kind=='cheap'
  else:
   es=limit if passive_kind=='rich' else bbp[hedge];el=aap[hedge] if passive_kind=='rich' else limit;short_passive=passive_kind=='rich';long_passive=passive_kind=='cheap'
  end=min(n-1,hedge+max(1,int(hold*1e9//STEP_NS)));exit_i=end;reason='timeout'
  for j in range(hedge+1,end+1):
   if not valid[j]:continue
   if rich_a:mark=.5*(1-aap[j]/es)+.5*(bbp[j]/el-1);conv=np.log(aap[j]/bbp[j])<=anchor[j]
   else:mark=.5*(1-bap[j]/es)+.5*(abp[j]/el-1);conv=np.log(bap[j]/abp[j])<=-anchor[j]
   if mark<=-.001 or conv:exit_i=j;reason='stop' if mark<=-.001 else 'convergence';break
  xs=aap[exit_i] if rich_a else bap[exit_i];xl=bbp[exit_i] if rich_a else abp[exit_i]
  row={'date':x.date.iloc[0],'window':x.window.iloc[0],'entry_ts':x.ts.iloc[e],'fill_ts':pd.Timestamp(fill_ns,tz='UTC'),'exit_ts':x.ts.iloc[exit_i],'passive_kind':passive_kind,'rich_leg':'SPY' if rich_a else 'IVV','entry_short':es,'entry_long':el,'exit_short':xs,'exit_long':xl,'short_passive':short_passive,'long_passive':long_passive,'reason':reason}
  for b in (0,1,2):
   k=b/10000;ies=es if short_passive else es*(1-k);iel=el if long_passive else el*(1+k);row[f'pnl_{b}bps']=.5*(1-xs*(1+k)/ies)+.5*(xl*(1-k)/iel-1)
  rows.append(row);i=exit_i+1
 return rows
def metrics(t,col):
 if t.empty:return {'trades':0,'net_return':0.,'average_trade_bps':0.,'win_rate':0.,'positive_windows':0,'negative_windows':0,'positive_months':0,'negative_months':0,'worst_window':0.}
 w=t.groupby(['date','window'])[col].sum();m=t.groupby('date')[col].sum();return {'trades':len(t),'net_return':float(t[col].sum()),'average_trade_bps':float(t[col].mean()*1e4),'win_rate':float((t[col]>0).mean()),'positive_windows':int((w>0).sum()),'negative_windows':int((w<0).sum()),'positive_months':int((m>0).sum()),'negative_months':int((m<0).sum()),'worst_window':float(w.min())}
def configs():return list(product((100,250),(30,60),(0,.05,.1),('rich','cheap'),(100,500),(5,30)))
def process_window(key):
 date,label=key;dest=OUT/'window_results'/f'{date}_{label}.parquet'
 if dest.exists():return str(dest)
 x=pd.read_parquet(R3/'synchronized_50ms.parquet',filters=[('date','==',date),('window','==',label)]).reset_index(drop=True);rawq,rawt=raw_window(date,label);rows=[]
 for age,anchor,threshold,kind,lifetime,hold in configs():
  z=simulate(x,rawq,rawt,age,anchor,threshold,lifetime,hold,kind)
  for r in z:r.update({'age_ms':age,'anchor_seconds':anchor,'threshold_bps':threshold,'passive_kind_cfg':kind,'lifetime_ms':lifetime,'hold_seconds':hold})
  rows.extend(z)
 dest.parent.mkdir(parents=True,exist_ok=True);pd.DataFrame(rows).to_parquet(dest,index=False);return str(dest)
def main():
 OUT.mkdir(parents=True,exist_ok=True);manifest=pd.read_parquet(R3/'manifest.parquet');keys=[(r.date,r.label) for r in manifest.itertuples()];paths=[]
 with ProcessPoolExecutor(max_workers=16) as pool:
  fs={pool.submit(process_window,k):k for k in keys}
  for i,f in enumerate(as_completed(fs),1):paths.append(f.result());print(f'completed_windows={i}/{len(keys)}',flush=True) if i%6==0 else None
 frames=[pd.read_parquet(p) for p in paths];all_t=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame();all_t.to_parquet(OUT/'trades.parquet',index=False);rows=[]
 for age,anchor,threshold,kind,lifetime,hold in configs():
  mask=(all_t.age_ms.eq(age)&all_t.anchor_seconds.eq(anchor)&all_t.threshold_bps.eq(threshold)&all_t.passive_kind_cfg.eq(kind)&all_t.lifetime_ms.eq(lifetime)&all_t.hold_seconds.eq(hold)) if len(all_t) else pd.Series([],dtype=bool);t=all_t[mask] if len(all_t) else all_t
  for b in (0,1,2):
   m=metrics(t,f'pnl_{b}bps');m.update({'age_ms':age,'anchor_seconds':anchor,'threshold_bps':threshold,'passive_kind':kind,'lifetime_ms':lifetime,'hold_seconds':hold,'additional_bps_per_market_side':b});rows.append(m)
 grid=pd.DataFrame(rows);grid.to_parquet(OUT/'grid.parquet',index=False);best={}
 for b in (0,1,2):
  z=grid[grid.additional_bps_per_market_side.eq(b)].sort_values('net_return',ascending=False).iloc[0];best[str(b)]={k:(v.item() if hasattr(v,'item') else v) for k,v in z.items()}
 report={'status':'completed','planned_variants':96,'executed_variants':int(len(grid)/3),'workers':16,'quote_size_multiplier':QSIZE_MULT,'best_by_cost':best,'maximum_loaded_date':str(pd.to_datetime(manifest.date).max().date()),'holdout_rows_loaded':0};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n');print(json.dumps(report,indent=2,default=str))
if __name__=='__main__':main()
