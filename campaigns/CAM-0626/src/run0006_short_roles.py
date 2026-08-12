from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];A=ROOT/'campaigns'/'CAM-0626'/'artifacts';OUT=A/'RUN-0006';OUT.mkdir(parents=True,exist_ok=True);KEY=['symbol','target_ts','role']
def qload():
 parts=[pd.read_parquet(p) for p in (A/'RUN-0003').glob('quotes_entry*.parquet') if p.stat().st_size];q=pd.concat(parts,ignore_index=True);q.target_ts=pd.to_datetime(q.target_ts,utc=True);q.quote_ts=pd.to_datetime(q.quote_ts,utc=True);q['delay']=(q.quote_ts-q.target_ts).dt.total_seconds();return q[(q.delay>=0)&(q.delay<=60)].sort_values(['delay','quote_ts']).drop_duplicates(KEY)
def main():
 t=pd.read_parquet(A/'RUN-0004'/'best_trades.parquet');t=t[t.side.eq('short')].copy();t.date=pd.to_datetime(t.date);t['target_ts']=pd.to_datetime(t.date.dt.strftime('%Y-%m-%d')+' 09:35').dt.tz_localize('America/New_York').dt.tz_convert('UTC');t['role']='entry_bid_after';q=qload();t=t.merge(q[KEY+['quote_ts','bid_price','ask_price']],on=KEY,how='left',validate='one_to_one');t['entry_fill']=t.bid_price;t['stop']=t.entry_fill*1.02;b=pd.read_parquet(A/'RUN-0003'/'selected_bars_1m.parquet');b.date=pd.to_datetime(b.date);b.ts=pd.to_datetime(b.ts,utc=True);groups={(d,s):g.sort_values('ts') for (d,s),g in b.groupby(['date','symbol'],sort=False)};rows=[]
 for x in t[t.entry_fill.notna()].itertuples():
  time_exit=pd.Timestamp(x.date).tz_localize('America/New_York')+pd.Timedelta(hours=10,minutes=35);time_exit=time_exit.tz_convert('UTC');g=groups.get((x.date,x.symbol));z=g[(g.ts>=x.quote_ts.floor('min'))&(g.ts<time_exit)&g.high.ge(x.stop)] if g is not None else pd.DataFrame()
  if len(z):target=pd.Timestamp(z.iloc[0].ts)+pd.Timedelta(minutes=1);reason='stop'
  else:target=time_exit;reason='time_exit'
  rows.append({'date':x.date,'symbol':x.symbol,'entry_fill':x.entry_fill,'entry_quote_ts':x.quote_ts,'stop':x.stop,'target_ts':target,'role':'exit_short_ask_after','exit_reason':reason,'weight':1.0})
 l=pd.DataFrame(rows);l.to_parquet(OUT/'ledger.parquet',index=False);l[KEY].drop_duplicates().to_parquet(OUT/'roles_exit.parquet',index=False);t.to_parquet(OUT/'entry_audit.parquet',index=False);print({'intended_days':len(t),'entry_matched':len(l),'entry_coverage':len(l)/len(t),'stops':int((l.exit_reason=='stop').sum()),'max_date':str(t.date.max().date()),'holdout_rows':0})
if __name__=='__main__':main()
