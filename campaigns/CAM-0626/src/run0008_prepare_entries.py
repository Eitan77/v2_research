from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];A=ROOT/'campaigns'/'CAM-0626'/'artifacts';OUT=A/'RUN-0008';OUT.mkdir(parents=True,exist_ok=True)
t=pd.read_parquet(A/'RUN-0005'/'best_trades.parquet');t['date']=pd.to_datetime(t.date);t['target_ts']=pd.to_datetime(t.date.dt.strftime('%Y-%m-%d')+' '+t.entry_time).dt.tz_localize('America/New_York').dt.tz_convert('UTC');t['role']='entry_invvar_after';r=t[['symbol','target_ts','role']].drop_duplicates();r.to_parquet(OUT/'roles_entry.parquet',index=False);print({'roles':len(r),'days':int(t.date.nunique()),'symbols':int(t.symbol.nunique()),'max_date':str(t.date.max().date())})
