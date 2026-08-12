from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0003'
l=pd.read_parquet(OUT/'quote_ledger.parquet');l['target_ts']=pd.to_datetime(l.target_ts,utc=True)
mask=l.phase.eq('exit')&l.exit_reason.eq('stop')
l.loc[mask,'target_ts']=l.loc[mask,'target_ts']+pd.Timedelta(minutes=5)
l.loc[l.phase.eq('exit'),'role']=l.loc[l.phase.eq('exit'),'side'].map(lambda s:f'exit_causal_{"ask" if s=="buy" else "bid"}_after')
q=l[l.phase.eq('exit')][['symbol','target_ts','role']].drop_duplicates()
q.to_parquet(OUT/'roles_exit_causal.parquet',index=False)
l.to_parquet(OUT/'quote_ledger_causal.parquet',index=False)
print({'exit_roles':len(q),'stop_roles_delayed':int(mask.sum()),'max_date':str(l.date.max().date())})
