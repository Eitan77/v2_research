from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];A=ROOT/'campaigns'/'CAM-0626'/'artifacts';OUT=A/'RUN-0008';OUT.mkdir(parents=True,exist_ok=True)
def entries():
 parts=[pd.read_parquet(p) for p in OUT.glob('quotes_entry*.parquet') if p.stat().st_size];q=pd.concat(parts,ignore_index=True);q.target_ts=pd.to_datetime(q.target_ts,utc=True);q.quote_ts=pd.to_datetime(q.quote_ts,utc=True);q['delay']=(q.quote_ts-q.target_ts).dt.total_seconds();return q[(q.delay>=0)&(q.delay<=60)].sort_values(['delay','quote_ts']).drop_duplicates(['symbol','target_ts'])
def main():
 t=pd.read_parquet(A/'RUN-0005'/'best_trades.parquet');t.date=pd.to_datetime(t.date);t['target_ts']=pd.to_datetime(t.date.dt.strftime('%Y-%m-%d')+' '+t.entry_time).dt.tz_localize('America/New_York').dt.tz_convert('UTC');q=entries();t=t.merge(q[['symbol','target_ts','quote_ts','bid_price','ask_price']],on=['symbol','target_ts'],how='left',validate='many_to_one');t['entry_fill']=np.where(t.side.eq('long'),t.ask_price,t.bid_price);t['package_executable']=t.groupby('date').entry_fill.transform(lambda x:x.notna().all());active=t[t.package_executable].copy();b=pd.read_parquet(A/'RUN-0003'/'selected_bars_1m.parquet');b.date=pd.to_datetime(b.date);b.ts=pd.to_datetime(b.ts,utc=True);groups={(d,s):g.sort_values('ts') for (d,s),g in b.groupby(['date','symbol'],sort=False)};rows=[]
 for x in active.itertuples():
  stop=x.entry_fill*(.98 if x.side=='long' else 1.02);g=groups.get((x.date,x.symbol));p=g[g.ts>=x.quote_ts.floor('min')] if g is not None else pd.DataFrame();z=p[p.low.le(stop)] if x.side=='long' and len(p) else (p[p.high.ge(stop)] if len(p) else p)
  if len(z):target=pd.Timestamp(z.iloc[0].ts)+pd.Timedelta(minutes=1);reason='stop'
  else:target=pd.Timestamp(x.date).tz_localize('America/New_York')+pd.Timedelta(hours=15,minutes=50);target=target.tz_convert('UTC');reason='time_exit'
  side='sell' if x.side=='long' else 'buy';role=f'exit_invvar_{"bid" if side=="sell" else "ask"}_after';rows.append({'date':x.date,'symbol':x.symbol,'side':x.side,'signed_weight':x.weight,'weight':abs(x.weight),'entry_fill':x.entry_fill,'entry_quote_ts':x.quote_ts,'stop':stop,'target_ts':target,'role':role,'exit_side':side,'exit_reason':reason})
 l=pd.DataFrame(rows);l.to_parquet(OUT/'ledger.parquet',index=False);l[['symbol','target_ts','role']].drop_duplicates().to_parquet(OUT/'roles_exit.parquet',index=False);t.to_parquet(OUT/'entry_audit.parquet',index=False);print({'intended_days':int(t.date.nunique()),'executable_package_days':int(active.date.nunique()),'cancelled_days':int(t.date.nunique()-active.date.nunique()),'active_trades':len(active),'stops':int((l.exit_reason=='stop').sum()),'max_date':str(t.date.max().date()),'holdout_rows':0})
if __name__=='__main__':main()
