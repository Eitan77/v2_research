from __future__ import annotations
import json
from pathlib import Path
import duckdb,pandas as pd

ROOT=Path(__file__).resolve().parents[3];CAM=ROOT/'campaigns'/'CAM-0639';OUT=CAM/'artifacts'/'RUN-0001';CAT=Path(r'D:\AlgoResearch\data\catalog.duckdb')

def main():
 OUT.mkdir(parents=True,exist_ok=True);c=duckdb.connect(str(CAT),read_only=True)
 q="""with b as (select date,try_cast(timestamp as timestamptz) ts,arg_max(open,try_cast(ingested_at as timestamp)) as open,arg_max(close,try_cast(ingested_at as timestamp)) as close from bars_1m where date between date '2021-05-01' and date '2026-04-30' and feed='sip' and adjustment='raw' and symbol='MU' and strftime(try_cast(timestamp as timestamptz) at time zone 'America/New_York','%H:%M') between '09:30' and '15:59' group by 1,2) select date,min(ts) first_bar,max(ts) last_bar,count(*) bars,arg_min(open,ts) open_0930,arg_max(close,ts) last_close from b group by date order by date"""
 x=c.execute(q).fetchdf();c.close();x['date']=pd.to_datetime(x.date);x['first_bar']=pd.to_datetime(x.first_bar,utc=True);x['last_bar']=pd.to_datetime(x.last_bar,utc=True)
 if x.empty or x.date.max()>pd.Timestamp('2026-04-30'):raise RuntimeError('boundary failure')
 rows=[]
 for i in range(len(x)-1):
  entry=x.loc[i,'last_bar']+pd.Timedelta(seconds=50);exit=x.loc[i+1,'first_bar'];
  if exit.date()>pd.Timestamp('2026-04-30',tz='UTC').date():raise RuntimeError('sealed exit role')
  rows.append({'entry_date':x.loc[i,'date'],'exit_date':x.loc[i+1,'date'],'entry_target':entry,'exit_target':exit,'entry_bar_close':x.loc[i,'last_close'],'exit_bar_open':x.loc[i+1,'open_0930'],'entry_session_bars':x.loc[i,'bars']})
 roles=pd.DataFrame(rows);roles.to_parquet(OUT/'roles.parquet',index=False);roles.to_csv(OUT/'roles.csv',index=False)
 report={'sessions':len(x),'overnights':len(roles),'min_entry_date':str(roles.entry_date.min().date()),'max_exit_date':str(roles.exit_date.max().date()),'early_close_entries':int((roles.entry_session_bars<390).sum()),'missing_open_values':int(roles.exit_bar_open.isna().sum()),'missing_close_values':int(roles.entry_bar_close.isna().sum()),'maximum_loaded_date':str(x.date.max().date()),'holdout_rows':0}
 (OUT/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
