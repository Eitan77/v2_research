from pathlib import Path
from datetime import datetime,time
from zoneinfo import ZoneInfo
import sys,numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(Path(__file__).parent));from run0002_timing_selectivity import simulate
OUT=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0003';P2=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0002';NY=ZoneInfo('America/New_York');OUT.mkdir(parents=True,exist_ok=True)
pairs=pd.read_parquet(ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0001'/'selection_query.parquet');pairs.date=pd.to_datetime(pairs.date);bars=pd.read_parquet(P2/'selected_bars.parquet');bars.date=pd.to_datetime(bars.date);d,t=simulate(pairs,bars,5,'1550','outer_half');rows=[]
for x in t.itertuples():
 for phase,clock,side in [('entry',x.entry_time,'buy' if x.side=='long' else 'sell'),('exit',x.exit_time,'sell' if x.side=='long' else 'buy')]:
  h,m=map(int,clock.split(':'));ts=pd.Timestamp(datetime.combine(pd.Timestamp(x.date).date(),time(h,m),tzinfo=NY)).tz_convert('UTC');rows.append({'date':pd.Timestamp(x.date),'symbol':x.symbol,'position_side':x.side,'phase':phase,'side':side,'weight':abs(x.weight),'target_ts':ts,'role':f'{phase}_{"ask" if side=="buy" else "bid"}_after','bar_gross_pnl':x.gross_pnl,'exit_reason':x.reason})
l=pd.DataFrame(rows);l.to_parquet(OUT/'quote_ledger.parquet',index=False)
for phase,g in l.groupby('phase'):g[['symbol','target_ts','role']].drop_duplicates().to_parquet(OUT/f'roles_{phase}.parquet',index=False)
d.to_parquet(OUT/'bar_daily.parquet',index=False);t.to_parquet(OUT/'bar_trades.parquet',index=False);print({'trades':len(t),'roles':len(l),'entry':int((l.phase=='entry').sum()),'exit':int((l.phase=='exit').sum()),'max_date':str(l.date.max().date())})
