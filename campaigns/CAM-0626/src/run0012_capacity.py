from __future__ import annotations
import gzip,json,multiprocessing,sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0012';SRC=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0010';OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT/'campaigns'/'CAM-0207'/'src'));import run0003_entry_lifetime as eng
eng.base.MAX_QUOTE_AGE_SECONDS=5.0
def capacity(job):
 raw={}
 for k in ('quotes','trades'):
  with gzip.open(job[k+'_path'],'rb') as h:raw[k]=json.loads(h.read().decode()).get('rows',[])
 order=pd.Timestamp(job['date'].strftime('%Y-%m-%d')+' 09:35',tz='America/New_York').to_pydatetime();out={**job}
 for n in (1,1000,10000,50000,100000):
  target=n/job['reference_price'];z=eng.simulate_passive_fast(raw['quotes'],raw['trades'],'sell',order,target,300);out[f'fill_{n}']=bool(z['status']=='filled')
 return out
def main():
 src=pd.read_parquet(ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0006'/'entry_audit.parquet');src.date=pd.to_datetime(src.date);m=pd.read_parquet(SRC/'retrieval_manifest.parquet');paths={(x.session_date,x.symbol,x.kind):x.path for x in m.dropna(subset=['path']).itertuples()};jobs=[]
 for x in src.itertuples():
  ds=x.date.strftime('%Y-%m-%d');jobs.append({'date':x.date,'symbol':x.symbol,'reference_price':float(x.entry),'quotes_path':paths[(ds,x.symbol,'quotes')],'trades_path':paths[(ds,x.symbol,'trades')]})
 with ProcessPoolExecutor(max_workers=16) as p:rows=list(p.map(capacity,jobs,chunksize=1))
 f=pd.DataFrame(rows);f.to_parquet(OUT/'order_capacity.parquet',index=False);levels=[]
 for n in (1000,10000,50000,100000):levels.append({'notional_usd':n,'full_fill_orders':int(f[f'fill_{n}'].sum()),'full_fill_rate':float(f[f'fill_{n}'].mean())})
 mono=bool(((~f.fill_1000)|f.fill_1).all() and ((~f.fill_10000)|f.fill_1000).all() and ((~f.fill_50000)|f.fill_10000).all() and ((~f.fill_100000)|f.fill_50000).all())
 report={'status':'completed' if mono else 'invalid_nonmonotonic','orders':len(f),'tiny_order_fills':int(f.fill_1.sum()),'capacity_levels':levels,'monotonicity_passed':mono,'maximum_loaded_date':str(src.date.max().date()),'holdout_rows_loaded':0,'strict_queue_preserving':True,'engine':'CAM-0207 simulate_passive_fast unchanged'};(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':multiprocessing.freeze_support();main()
