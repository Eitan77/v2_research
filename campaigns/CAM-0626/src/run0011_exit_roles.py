from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];A=ROOT/'campaigns'/'CAM-0626'/'artifacts';OUT=A/'RUN-0011';OUT.mkdir(parents=True,exist_ok=True);l=pd.read_parquet(A/'RUN-0010'/'exit_ledger.parquet');l.date=pd.to_datetime(l.date);b=pd.read_parquet(A/'RUN-0003'/'selected_bars_1m.parquet',columns=['date','symbol','ts','high']);b.date=pd.to_datetime(b.date);b=b.merge(l[['date','symbol']].drop_duplicates(),on=['date','symbol'],how='inner');b.ts=pd.to_datetime(b.ts,utc=True);groups={(d,s):g for (d,s),g in b.groupby(['date','symbol'],sort=False)};rows=[]
for x in l.itertuples():
 time_exit=pd.Timestamp(x.date.strftime('%Y-%m-%d')+' 10:35',tz='America/New_York').tz_convert('UTC');g=groups[(x.date,x.symbol)]
 for stop_pct in (.5,1.,2.,3.,5.):
  z=g[(g.ts>=x.entry_fill_ts.floor('min'))&(g.ts<time_exit)&g.high.ge(x.entry_fill*(1+stop_pct/100))].sort_values('ts');target=pd.Timestamp(z.iloc[0].ts)+pd.Timedelta(minutes=1) if len(z) else time_exit;role=f'exit_s{stop_pct}_ask_after';rows.append({'date':x.date,'symbol':x.symbol,'entry_fill':x.entry_fill,'entry_fill_ts':x.entry_fill_ts,'stop_pct':stop_pct,'target_ts':target,'role':role,'exit_reason':'stop' if len(z) else 'time_exit'})
z=pd.DataFrame(rows);z.to_parquet(OUT/'ledger.parquet',index=False);z[['symbol','target_ts','role']].drop_duplicates().to_parquet(OUT/'roles.parquet',index=False);print({'roles':len(z),'days':int(z.date.nunique()),'stops_by_width':z.groupby('stop_pct').exit_reason.apply(lambda x:int(x.eq('stop').sum())).to_dict(),'max_date':str(z.date.max().date()),'holdout_rows':0})
