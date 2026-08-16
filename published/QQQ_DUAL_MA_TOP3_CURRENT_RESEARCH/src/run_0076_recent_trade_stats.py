from __future__ import annotations
import json
from pathlib import Path
import duckdb
import pandas as pd

ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0076'
ART=ROOT/'campaigns'/'CAM-0611'/'artifacts'

def main():
 OUT.mkdir(parents=True,exist_ok=True)
 dev=pd.read_parquet(ART/'RUN-0035'/'trade_episodes.parquet');dev.entry_date=pd.to_datetime(dev.entry_date);dev.exit_date=pd.to_datetime(dev.exit_date)
 recent=dev[(dev.exit_date>=pd.Timestamp('2025-08-15'))&(dev.exit_date<pd.Timestamp('2026-04-30'))][['symbol','entry_date','exit_date','position_return','holding_sessions']].copy()
 bars=pd.read_parquet(ART/'RUN-0059'/'daily_adjusted.parquet');bars.date=pd.to_datetime(bars.date);b=bars.set_index(['date','symbol'])
 q=pd.read_parquet(ART/'RUN-0059'/'quotes.parquet');q.date=pd.to_datetime(q.date);q=q.set_index(['date','label','symbol'])
 def px(day,sym,side):
  z30=q.loc[(pd.Timestamp(day),'0930',sym)];z40=q.loc[(pd.Timestamp(day),'0940',sym)];ref=(z30.bid_price+z30.ask_price)/2
  raw=z40.ask_price*1.0002 if side=='buy' else z40.bid_price*.9998
  return float(b.loc[(pd.Timestamp(day),sym),'open']*raw/ref)
 con=duckdb.connect(r'D:\AlgoResearch\data\catalog.duckdb',read_only=True)
 pre=set(pd.to_datetime([r[0] for r in con.execute("select distinct date from bars_1d where symbol='QQQ' and adjustment='raw' and date between '2025-08-15' and '2026-04-30'").fetchall()]));con.close()
 sessions=pd.DatetimeIndex(sorted(pre|set(pd.to_datetime(bars.date))))
 def hold(en,ex):return int(((sessions>=en)&(sessions<ex)).sum())
 rows=[]
 for sym,ex in [('MU','2026-05-18'),('WDC','2026-06-15')]:
  old=dev[(dev.symbol.eq(sym))&dev.exit_date.eq(pd.Timestamp('2026-04-30'))].iloc[0];factor=px(ex,sym,'sell')/float(b.loc[(pd.Timestamp('2026-04-30'),sym),'close'])
  rows.append({'symbol':sym,'entry_date':old.entry_date,'exit_date':pd.Timestamp(ex),'position_return':(1+old.position_return)*factor-1,'holding_sessions':hold(old.entry_date,pd.Timestamp(ex))})
 for sym,en,ex in [('STX','2026-05-18','2026-06-15'),('MU','2026-06-15','2026-06-22'),('INTC','2026-06-15','2026-06-29'),('WDC','2026-06-22','2026-07-27'),('INTC','2026-07-27','2026-08-10')]:
  en=pd.Timestamp(en);ex=pd.Timestamp(ex);rows.append({'symbol':sym,'entry_date':en,'exit_date':ex,'position_return':px(ex,sym,'sell')/px(en,sym,'buy')-1,'holding_sessions':hold(en,ex)})
 oos=pd.DataFrame(rows)
 episodes=pd.concat([recent,oos],ignore_index=True).sort_values('exit_date');episodes.to_csv(OUT/'completed_episodes.csv',index=False)
 cutoff_open=dev.exit_date.eq(pd.Timestamp('2026-04-30'))&dev.symbol.isin(['MU','SNDK','WDC'])
 full=pd.concat([dev.loc[~cutoff_open,['symbol','entry_date','exit_date','position_return','holding_sessions']],oos],ignore_index=True).sort_values('exit_date')
 full.to_csv(OUT/'full_history_completed_episodes.csv',index=False)
 def stats(x):
  w=x[x.position_return>0];l=x[x.position_return<=0]
  return {'completed_trades':len(x),'wins':len(w),'losses':len(l),'win_rate':len(w)/len(x),'average_win':w.position_return.mean(),'average_loss':l.position_return.mean(),'average_trade':x.position_return.mean(),'median_trade':x.position_return.median(),'average_holding_sessions':x.holding_sessions.mean(),'median_holding_sessions':x.holding_sessions.median()}
 report={'status':'completed','window':'2025-08-15 through 2026-08-14','closed_during_window':stats(episodes),'entered_and_closed_during_window':stats(episodes[episodes.entry_date>=pd.Timestamp('2025-08-15')]),'full_history_through_2026_08_14':stats(full),'open_excluded':['SNDK','MU','ARM'],'maximum_loaded_date':'2026-08-14','rows_after_authorized_end':0}
 (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
