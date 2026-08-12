from __future__ import annotations
import gzip,json,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];A=ROOT/'campaigns'/'CAM-0626'/'artifacts';OUT=A/'RUN-0010';RAW=OUT/'raw_api';OUT.mkdir(parents=True,exist_ok=True);RAW.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0207'/'src'));import run0003_entry_lifetime as eng
eng.ARTIFACT_DIR=OUT;eng.RAW_DIR=RAW;eng.ENTRY_LIFETIME_SECONDS=300;eng.CACHE_VERSION='cam0626_run0010_v1';eng.base.ENTRY_OFFSET=5;eng.base.MAX_QUOTE_AGE_SECONDS=5.0
def main():
 src=pd.read_parquet(A/'RUN-0006'/'entry_audit.parquet');src.date=pd.to_datetime(src.date);src=src[['date','symbol','bid_price']].copy();jobs=[{'session_date':d.strftime('%Y-%m-%d'),'symbol':s,'window':'entry','kind':k} for d,s in src[['date','symbol']].itertuples(index=False,name=None) for k in ('quotes','trades')];manifest=[]
 with ThreadPoolExecutor(max_workers=16) as pool:
  fs=[pool.submit(eng.fetch_window,j) for j in jobs]
  for i,f in enumerate(as_completed(fs),1):
   try:manifest.append(f.result())
   except Exception as e:manifest.append({'error':str(e)})
   if i%100==0:print(f'completed={i}/{len(fs)}',flush=True)
 pd.DataFrame(manifest).to_parquet(OUT/'retrieval_manifest.parquet',index=False);bad=sum('error' in x for x in manifest);lookup={}
 for x in manifest:
  if 'error' in x:continue
  with gzip.open(x['path'],'rb') as h:p=json.loads(h.read().decode())
  lookup[(x['session_date'],x['symbol'],x['kind'])]=p.get('rows',[])
 fills=[]
 for x in src.itertuples():
  ds=x.date.strftime('%Y-%m-%d');order=eng.base.session_timestamp(ds,5);target=1/float(x.bid_price) if np.isfinite(x.bid_price) and x.bid_price>0 else 0;z=eng.simulate_passive_fast(lookup.get((ds,x.symbol,'quotes'),[]),lookup.get((ds,x.symbol,'trades'),[]),'sell',order,target,300);fills.append({'date':x.date,'symbol':x.symbol,'target_qty':target,**z})
 f=pd.DataFrame(fills);f.to_parquet(OUT/'passive_fills.parquet',index=False);report={'status':'retrieval_blocked' if bad else 'completed_fill_gate','jobs':len(jobs),'failed_jobs':bad,'orders':len(f),'full_fills':int(f.status.eq('filled').sum()),'partial_fills':int(f.status.eq('partial').sum()),'no_fills':int((f.passive_fill_qty<=0).sum()),'full_fill_rate':float(f.status.eq('filled').mean()),'any_fill_rate':float((f.passive_fill_qty>0).mean()),'mean_fill_fraction':float((f.passive_fill_qty/f.target_qty).replace([np.inf,-np.inf],np.nan).fillna(0).mean()),'maximum_loaded_date':str(src.date.max().date()),'holdout_rows_loaded':0,'quote_touch_is_fill':False,'strict_queue_preserving':True};(OUT/'fill_report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
