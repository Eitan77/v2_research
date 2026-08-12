from __future__ import annotations
from datetime import datetime,time
from pathlib import Path
from zoneinfo import ZoneInfo
import duckdb,numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0003';NY=ZoneInfo('America/New_York');KEY=['symbol','target_ts','role']

def quotes(pattern):
 parts=[pd.read_parquet(p) for p in OUT.glob(pattern) if p.stat().st_size]
 q=pd.concat(parts,ignore_index=True);q.target_ts=pd.to_datetime(q.target_ts,utc=True);q.quote_ts=pd.to_datetime(q.quote_ts,utc=True);q['delay']=(q.quote_ts-q.target_ts).dt.total_seconds();return q[(q.delay>=0)&(q.delay<=60)].sort_values(['delay','quote_ts']).drop_duplicates(KEY)

def minute_bars(symbols):
 cache=OUT/'selected_bars_1m.parquet'
 if cache.exists():return pd.read_parquet(cache)
 c=duckdb.connect(r'D:\AlgoResearch\data\catalog.duckdb',read_only=True);c.execute('set threads=16');c.execute("set preserve_insertion_order=false");syms=','.join("'"+s.replace("'","''")+"'" for s in sorted(symbols))
 q=f'''select date,symbol,try_cast(timestamp as timestamptz) as ts,
 arg_max(open,try_cast(ingested_at as timestamp)) as open,arg_max(high,try_cast(ingested_at as timestamp)) as high,arg_max(low,try_cast(ingested_at as timestamp)) as low,arg_max(close,try_cast(ingested_at as timestamp)) as close
 from bars_1m where date between date '2024-05-01' and date '2026-04-30' and feed='sip' and adjustment='raw' and symbol in ({syms})
 and strftime(try_cast(timestamp as timestamptz) at time zone 'America/New_York','%H:%M') between '09:35' and '15:50' group by 1,2,3'''
 b=c.execute(q).fetchdf();c.close();b.date=pd.to_datetime(b.date);b.ts=pd.to_datetime(b.ts,utc=True);b.to_parquet(cache,index=False);return b

def main():
 l=pd.read_parquet(OUT/'quote_ledger.parquet');l.target_ts=pd.to_datetime(l.target_ts,utc=True);entry=l[l.phase.eq('entry')].copy();qe=quotes('quotes_entry*.parquet');entry=entry.merge(qe[KEY+['quote_ts','bid_price','ask_price']],on=KEY,how='left',validate='one_to_one');entry['entry_fill']=np.where(entry.side.eq('buy'),entry.ask_price,entry.bid_price)
 day_ok=entry.groupby('date').entry_fill.transform(lambda x:x.notna().all());entry['package_executable']=day_ok;active=entry[day_ok].copy();bars=minute_bars(active.symbol.unique());groups={(d,s):g.sort_values('ts') for (d,s),g in bars.groupby(['date','symbol'],sort=False)};roles=[];diag=[]
 for x in active.itertuples():
  g=groups.get((pd.Timestamp(x.date),x.symbol));stop=x.entry_fill*(.98 if x.position_side=='long' else 1.02);hit=None
  if g is not None:
   p=g[g.ts>=x.quote_ts.floor('min')]
   z=p[p.low.le(stop)] if x.position_side=='long' else p[p.high.ge(stop)]
   if len(z):hit=z.iloc[0]
  if hit is None:
   target=pd.Timestamp(datetime.combine(pd.Timestamp(x.date).date(),time(15,50),tzinfo=NY)).tz_convert('UTC');reason='time_exit'
  else:
   target=pd.Timestamp(hit.ts)+pd.Timedelta(minutes=1);reason='stop'
  side='sell' if x.position_side=='long' else 'buy';role=f'exit_minute_{"bid" if side=="sell" else "ask"}_after';roles.append({'symbol':x.symbol,'target_ts':target,'role':role});diag.append({'date':x.date,'symbol':x.symbol,'position_side':x.position_side,'weight':x.weight,'entry_fill':x.entry_fill,'entry_quote_ts':x.quote_ts,'stop':stop,'exit_target_ts':target,'exit_reason_minute':reason,'exit_side':side,'exit_role':role})
 pd.DataFrame(roles).drop_duplicates().to_parquet(OUT/'roles_exit_minute.parquet',index=False);pd.DataFrame(diag).to_parquet(OUT/'minute_stop_ledger.parquet',index=False);entry.to_parquet(OUT/'entry_package_audit.parquet',index=False)
 print({'intended_days':int(entry.date.nunique()),'executable_package_days':int(entry.loc[day_ok,'date'].nunique()),'cancelled_package_days':int(entry.loc[~day_ok,'date'].nunique()),'active_trades':len(active),'minute_bar_rows':len(bars),'minute_stops':sum(x['exit_reason_minute']=='stop' for x in diag),'max_date':str(bars.date.max().date()),'holdout_rows':int((bars.date>pd.Timestamp('2026-04-30')).sum())})
if __name__=='__main__':main()
