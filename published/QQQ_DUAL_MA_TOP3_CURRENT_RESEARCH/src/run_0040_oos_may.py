from __future__ import annotations
import argparse,json,sys
from datetime import datetime,time
from pathlib import Path
from zoneinfo import ZoneInfo
import duckdb,numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"))
from suite_core import load_panels,trailing_return
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0040";NY=ZoneInfo("America/New_York");KEY=["symbol","target_ts","role"]
def build():
 p=load_panels()["qqq"];daily=pd.read_parquet(OUT/"daily_all_adjusted_apr_may.parquet");daily.date=pd.to_datetime(daily.date);may_dates=pd.DatetimeIndex(sorted(daily.loc[daily.date.dt.month.eq(5),"date"].unique()));symbols=p.symbols.astype(str);cols=p.symbol_to_col;n=len(may_dates);op=np.full((n,len(symbols)),np.nan);cl=np.full_like(op,np.nan);vol=np.full_like(op,np.nan);rawdv=np.full_like(op,np.nan)
 con=duckdb.connect(r"D:\AlgoResearch\data\catalog.duckdb",read_only=True);mem=con.execute("select try_cast(date as date) date,symbol,arg_max(is_member,try_cast(ingested_at as timestamp)) is_member from qqq_pit_membership_daily where try_cast(date as date) between date '2026-05-01' and date '2026-05-31' group by 1,2").fetchdf();raw=con.execute("select date,symbol,arg_max(close,try_cast(ingested_at as timestamp)) as raw_close,arg_max(volume,try_cast(ingested_at as timestamp)) as raw_volume from bars_1d where date between date '2026-05-01' and date '2026-05-31' and feed='sip' and adjustment='raw' group by 1,2").fetchdf();con.close();mem.date=pd.to_datetime(mem.date);raw.date=pd.to_datetime(raw.date);member=np.zeros_like(op,dtype=bool)
 for s in symbols:
  if s not in cols:continue
  c=cols[s];a=daily[(daily.symbol.eq(s))&(daily.date.eq(pd.Timestamp("2026-04-30")))]
  if a.empty or not np.isfinite(p.adj_close[-1,c]):continue
  scale=float(p.adj_close[-1,c]/a.close.iloc[0]);g=daily[(daily.symbol.eq(s))&daily.date.isin(may_dates)].set_index("date")
  for i,d in enumerate(may_dates):
   if d in g.index:op[i,c]=float(g.loc[d,"open"])*scale;cl[i,c]=float(g.loc[d,"close"])*scale;vol[i,c]=float(g.loc[d,"volume"])
   z=raw[(raw.symbol.eq(s))&raw.date.eq(d)]
   if len(z):rawdv[i,c]=float(z.raw_close.iloc[0])*float(z.raw_volume.iloc[0])
   z=mem[(mem.symbol.eq(s))&mem.date.eq(d)];member[i,c]=bool(z.is_member.iloc[0]) if len(z) else False
 hist_close=pd.DataFrame(p.adj_close,index=p.dates);comb_close=pd.concat([hist_close,pd.DataFrame(cl,index=may_dates)]);hist_dv=pd.DataFrame(p.raw_close*p.volume,index=p.dates);comb_dv=pd.concat([hist_dv,pd.DataFrame(rawdv,index=may_dates)]);s50=comb_close.rolling(50,min_periods=50).mean();s200=comb_close.rolling(200,min_periods=200).mean();dv63=comb_dv.rolling(63,min_periods=32).median();tri_hist=pd.DataFrame(p.total_return_index,index=p.dates);tri=tri_hist.copy();last=tri.iloc[-1].to_numpy();prev=comb_close.loc[p.dates[-1]].to_numpy()
 for d in may_dates:
  cur=comb_close.loc[d].to_numpy();step=np.divide(cur,prev,out=np.ones_like(cur),where=np.isfinite(cur)&np.isfinite(prev)&(prev>0));last=last*step;tri.loc[d]=last;prev=cur
 score=np.full((n,len(symbols)),np.nan)
 for i,d in enumerate(may_dates):
  loc=tri.index.get_loc(d);score[i]=tri.iloc[loc-21].to_numpy()/tri.iloc[loc-147].to_numpy()-1
 targets=[];signal_idx=[]
 periods=may_dates.to_period("W-FRI")
 for i in range(n):
  if i==n-1 or periods[i+1]!=periods[i]:signal_idx.append(i)
 carry=np.load(ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0034"/"weights_control.npy")[-1].copy();current=carry.copy();target_by_signal={}
 for i in signal_idx:
  d=may_dates[i];ready=member[i]&np.isfinite(cl[i])&(s50.loc[d].to_numpy()>s200.loc[d].to_numpy())&np.isfinite(score[i])&np.isfinite(dv63.loc[d].to_numpy());eligible=np.flatnonzero(ready);k=max(1,int(np.ceil(len(eligible)*.5))) if len(eligible) else 0;liq=eligible[np.argsort(dv63.loc[d].to_numpy()[eligible])[-k:]] if k else np.array([],int);chosen=liq[np.argsort(score[i,liq])[-min(3,len(liq)):]] if len(liq) else np.array([],int);t=np.zeros(len(symbols));t[chosen]=1/len(chosen) if len(chosen) else 0;target_by_signal[i]=t;targets.append({"signal_date":str(d.date()),"eligible":len(eligible),"liquid":len(liq),"selected":[str(symbols[c]) for c in chosen]})
 executed=np.zeros_like(op);current=carry.copy();pending={i+1:t for i,t in target_by_signal.items() if i+1<n}
 for i in range(n):
  if i in pending:current=pending[i]
  executed[i]=current
 gross=np.zeros(n)
 for i in range(n):
  if i<n-1:gross[i]=np.nansum(executed[i]*(np.divide(op[i+1],op[i],out=np.ones(len(symbols)),where=np.isfinite(op[i+1])&np.isfinite(op[i])&(op[i]>0))-1))
  else:gross[i]=np.nansum(executed[i]*(np.divide(cl[i],op[i],out=np.ones(len(symbols)),where=np.isfinite(cl[i])&np.isfinite(op[i])&(op[i]>0))-1))
 rows=[];prevw=carry.copy()
 for i in range(n):
  if i not in pending:continue
  delta=executed[i]-prevw
  for c in np.flatnonzero(np.abs(delta)>1e-12):
   side="buy" if delta[c]>0 else "sell"
   for label,clock in (("0930",(9,30)),("0940",(9,40))):rows.append({"label":label,"session_date":may_dates[i],"symbol":str(symbols[c]),"side":side,"delta_weight":float(abs(delta[c])),"target_ts":pd.Timestamp(datetime.combine(may_dates[i].date(),time(*clock),tzinfo=NY)).tz_convert("UTC"),"role":"entry_ask_after" if side=="buy" else "exit_bid_after"})
  prevw=executed[i].copy()
 ledger=pd.DataFrame(rows);ledger.to_parquet(OUT/"quote_ledger.parquet",index=False)
 for label,g in ledger.groupby("label"):g[KEY].drop_duplicates().to_parquet(OUT/f"roles_{label}.parquet",index=False)
 pd.DataFrame({"date":may_dates,"gross_pnl":gross}).to_parquet(OUT/"gross_daily.parquet",index=False);np.save(OUT/"executed_weights.npy",executed);(OUT/"targets.json").write_text(json.dumps(targets,indent=2)+"\n");print(json.dumps({"dates":len(may_dates),"max_date":str(may_dates.max().date()),"targets":targets,"quote_roles":len(ledger)},indent=2))
def replay():
 ledger=pd.read_parquet(OUT/"quote_ledger.parquet");ledger["target_ts"]=pd.to_datetime(ledger.target_ts,utc=True)
 q30=pd.read_parquet(OUT/"quotes_0930_5s.parquet");q40=pd.read_parquet(OUT/"quotes_0940_5s.parquet")
 for q in (q30,q40):q["target_ts"]=pd.to_datetime(q.target_ts,utc=True)
 refs=q30.assign(reference_mid=(q30.bid_price+q30.ask_price)/2)[["symbol","reference_mid"]]
 fills=q40[["symbol","bid_price","ask_price","quote_ts"]]
 trades=ledger[ledger.label.eq("0940")].merge(refs,on="symbol",how="left",validate="one_to_one").merge(fills,on="symbol",how="left",validate="one_to_one")
 trades["fill_price"]=np.where(trades.side.eq("buy"),trades.ask_price,trades.bid_price)
 trades["execution_drag"]=np.where(trades.side.eq("buy"),trades.fill_price/trades.reference_mid-1,1-trades.fill_price/trades.reference_mid)
 trades["commission_drag"]=trades.delta_weight*0.0002
 trades["cost"]=trades.delta_weight*trades.execution_drag+trades.commission_drag
 if trades[["reference_mid","fill_price","cost"]].isna().any().any():raise RuntimeError("incomplete quote replay")
 daily=pd.read_parquet(OUT/"gross_daily.parquet");daily.date=pd.to_datetime(daily.date);costs=trades.groupby("session_date").cost.sum();daily["quote_and_2bps_cost"]=daily.date.map(costs).fillna(0.0);daily["net_pnl"]=daily.gross_pnl-daily.quote_and_2bps_cost;daily["equity"]=1+daily.net_pnl.cumsum();daily["drawdown"]=daily.equity/daily.equity.cummax()-1
 weekly=daily.set_index("date").net_pnl.resample("W-FRI").sum()
 report={"status":"completed","authorized_window":{"start":"2026-05-01","end":"2026-05-31","maximum_loaded_date":str(daily.date.max().date()),"rows_after_authorized_end":0},"quote_coverage":1.0,"return_net_fixed_base_pct":float(100*daily.net_pnl.sum()),"return_gross_pct":float(100*daily.gross_pnl.sum()),"max_drawdown_peak_relative_pct":float(-100*daily.drawdown.min()),"positive_days":int((daily.net_pnl>0).sum()),"negative_days":int((daily.net_pnl<0).sum()),"best_day_pct":float(100*daily.net_pnl.max()),"worst_day_pct":float(100*daily.net_pnl.min()),"weekly_pnl_pct":{str(k.date()):float(100*v) for k,v in weekly.items()},"turnover_weight":float(trades.delta_weight.sum()),"quote_execution_drag_pct":float(100*(trades.delta_weight*trades.execution_drag).sum()),"explicit_2bps_drag_pct":float(100*trades.commission_drag.sum()),"changed_names":trades[["session_date","symbol","side","delta_weight","reference_mid","fill_price","execution_drag","cost"]].to_dict("records")}
 trades.to_parquet(OUT/"quote_fill_ledger.parquet",index=False);daily.to_parquet(OUT/"oos_daily_net.parquet",index=False);(OUT/"oos_report.json").write_text(json.dumps(report,indent=2,default=str)+"\n");print(json.dumps(report,indent=2,default=str))
if __name__=="__main__":
 ap=argparse.ArgumentParser();ap.add_argument("phase",choices=("build","replay"));a=ap.parse_args();globals()[a.phase]()
