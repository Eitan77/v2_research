from __future__ import annotations
import argparse,json,sys
from datetime import datetime,time
from pathlib import Path
from zoneinfo import ZoneInfo
import duckdb,numpy as np,pandas as pd

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"))
from suite_core import load_panels
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0042"
NY=ZoneInfo("America/New_York")
KEY=["symbol","target_ts","role"]

def build():
 p=load_panels()["qqq"]
 if pd.Timestamp(p.dates.max())!=pd.Timestamp("2026-04-30"):raise RuntimeError("discovery panel cutoff mismatch")
 daily=pd.read_parquet(OUT/"daily_all_adjusted_apr_augtd.parquet");daily.date=pd.to_datetime(daily.date)
 ext_dates=pd.DatetimeIndex(sorted(daily.loc[daily.date.between("2026-05-01","2026-08-10"),"date"].unique()))
 eval_dates=ext_dates[ext_dates>=pd.Timestamp("2026-08-01")]
 if eval_dates.max()>pd.Timestamp("2026-08-10"):raise RuntimeError("authorized range breached")
 symbols=p.symbols.astype(str);cols=p.symbol_to_col;n=len(ext_dates);m=len(symbols)
 op=np.full((n,m),np.nan);cl=np.full_like(op,np.nan);rawdv=np.full_like(op,np.nan);member=np.zeros_like(op,dtype=bool)
 con=duckdb.connect(r"D:\AlgoResearch\data\catalog.duckdb",read_only=True)
 mem=con.execute("select try_cast(date as date) date,symbol,arg_max(is_member,try_cast(ingested_at as timestamp)) is_member from qqq_pit_membership_daily where try_cast(date as date) between date '2026-05-01' and date '2026-08-10' group by 1,2").fetchdf()
 raw=con.execute("select date,symbol,arg_max(close,try_cast(ingested_at as timestamp)) as raw_close,arg_max(volume,try_cast(ingested_at as timestamp)) as raw_volume from bars_1d where date between date '2026-05-01' and date '2026-08-10' and feed='sip' and adjustment='raw' group by 1,2").fetchdf();con.close()
 mem.date=pd.to_datetime(mem.date);raw.date=pd.to_datetime(raw.date);membership_max=pd.Timestamp(mem.date.max())
 for s in symbols:
  c=cols[s];anchor=daily[(daily.symbol.eq(s))&daily.date.eq(pd.Timestamp("2026-04-30"))]
  if anchor.empty or not np.isfinite(p.adj_close[-1,c]):continue
  scale=float(p.adj_close[-1,c]/anchor.close.iloc[0]);g=daily[daily.symbol.eq(s)].set_index("date")
  rg=raw[raw.symbol.eq(s)].set_index("date");mg=mem[mem.symbol.eq(s)].set_index("date").is_member.reindex(ext_dates).ffill().fillna(False)
  for i,d in enumerate(ext_dates):
   if d in g.index:op[i,c]=float(g.loc[d,"open"])*scale;cl[i,c]=float(g.loc[d,"close"])*scale
   if d in rg.index:rawdv[i,c]=float(rg.loc[d,"raw_close"])*float(rg.loc[d,"raw_volume"])
   member[i,c]=bool(mg.loc[d])
 hist_close=pd.DataFrame(p.adj_close,index=p.dates);comb_close=pd.concat([hist_close,pd.DataFrame(cl,index=ext_dates)])
 hist_dv=pd.DataFrame(p.raw_close*p.volume,index=p.dates);comb_dv=pd.concat([hist_dv,pd.DataFrame(rawdv,index=ext_dates)])
 s50=comb_close.rolling(50,min_periods=50).mean();s200=comb_close.rolling(200,min_periods=200).mean();dv63=comb_dv.rolling(63,min_periods=32).median()
 tri=pd.DataFrame(p.total_return_index,index=p.dates);last=tri.iloc[-1].to_numpy();prev=comb_close.loc[p.dates[-1]].to_numpy()
 for d in ext_dates:
  cur=comb_close.loc[d].to_numpy();step=np.divide(cur,prev,out=np.ones_like(cur),where=np.isfinite(cur)&np.isfinite(prev)&(prev>0));last=last*step;tri.loc[d]=last;prev=cur
 scores={}
 for d in ext_dates:
  loc=tri.index.get_loc(d);scores[d]=tri.iloc[loc-21].to_numpy()/tri.iloc[loc-147].to_numpy()-1
 periods=ext_dates.to_period("W-FRI");signal_idx=[i for i in range(n-1) if periods[i+1]!=periods[i]]
 if ext_dates[-1].weekday()>=4:signal_idx.append(n-1)
 target_by_signal={};targets=[]
 for i in signal_idx:
  d=ext_dates[i];score=scores[d];ready=member[i]&np.isfinite(cl[i])&(s50.loc[d].to_numpy()>s200.loc[d].to_numpy())&np.isfinite(score)&np.isfinite(dv63.loc[d].to_numpy());eligible=np.flatnonzero(ready);k=max(1,int(np.ceil(len(eligible)*.5))) if len(eligible) else 0;liq=eligible[np.argsort(dv63.loc[d].to_numpy()[eligible])[-k:]] if k else np.array([],int);chosen=liq[np.argsort(score[liq])[-min(3,len(liq)):]] if len(liq) else np.array([],int);t=np.zeros(m);t[chosen]=1/len(chosen) if len(chosen) else 0;target_by_signal[i]=t;targets.append({"signal_date":str(d.date()),"eligible":len(eligible),"liquid":len(liq),"selected":[str(symbols[c]) for c in chosen]})
 carry=np.load(ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0041"/"executed_weights.npy")[-1].copy();eval_pos={d:i for i,d in enumerate(eval_dates)}
 pending={ext_dates[i+1]:t for i,t in target_by_signal.items() if i+1<n and ext_dates[i+1] in eval_pos}
 executed=np.zeros((len(eval_dates),m));current=carry.copy()
 for i,d in enumerate(eval_dates):
  if d in pending:current=pending[d]
  executed[i]=current
 ext_pos={d:i for i,d in enumerate(ext_dates)};gross=np.zeros(len(eval_dates))
 for i,d in enumerate(eval_dates):
  j=ext_pos[d]
  if i<len(eval_dates)-1:r=np.divide(op[j+1],op[j],out=np.ones(m),where=np.isfinite(op[j+1])&np.isfinite(op[j])&(op[j]>0))-1
  else:r=np.divide(cl[j],op[j],out=np.ones(m),where=np.isfinite(cl[j])&np.isfinite(op[j])&(op[j]>0))-1
  gross[i]=np.nansum(executed[i]*r)
 rows=[];prevw=carry.copy()
 for i,d in enumerate(eval_dates):
  if d not in pending:continue
  delta=executed[i]-prevw
  for c in np.flatnonzero(np.abs(delta)>1e-12):
   side="buy" if delta[c]>0 else "sell"
   for label,clock in (("0930",(9,30)),("0940",(9,40))):rows.append({"label":label,"session_date":d,"symbol":str(symbols[c]),"side":side,"delta_weight":float(abs(delta[c])),"target_ts":pd.Timestamp(datetime.combine(d.date(),time(*clock),tzinfo=NY)).tz_convert("UTC"),"role":"entry_ask_after" if side=="buy" else "exit_bid_after"})
  prevw=executed[i].copy()
 ledger=pd.DataFrame(rows);ledger.to_parquet(OUT/"quote_ledger.parquet",index=False)
 for label,g in ledger.groupby("label"):g[KEY].drop_duplicates().to_parquet(OUT/f"roles_{label}.parquet",index=False)
 pd.DataFrame({"date":eval_dates,"gross_pnl":gross}).to_parquet(OUT/"gross_daily.parquet",index=False);np.save(OUT/"executed_weights.npy",executed);(OUT/"targets.json").write_text(json.dumps(targets,indent=2)+"\n")
 build_report={"status":"passed","discovery_maximum_date":str(pd.Timestamp(p.dates.max()).date()),"evaluation_minimum_date":str(eval_dates.min().date()),"evaluation_maximum_date":str(eval_dates.max().date()),"rows_after_authorized_end":0,"membership_maximum_date":str(membership_max.date()),"membership_carry_forward_sessions":int((eval_dates>membership_max).sum()),"targets":targets,"quote_roles":len(ledger)};(OUT/"build_report.json").write_text(json.dumps(build_report,indent=2)+"\n");print(json.dumps(build_report,indent=2))

def replay():
 ledger=pd.read_parquet(OUT/"quote_ledger.parquet");ledger["target_ts"]=pd.to_datetime(ledger.target_ts,utc=True)
 q30=pd.read_parquet(OUT/"quotes_0930_5s.parquet");q40=pd.read_parquet(OUT/"quotes_0940_5s.parquet")
 for q in (q30,q40):q["target_ts"]=pd.to_datetime(q.target_ts,utc=True)
 q30["session_date"]=q30.target_ts.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize();q40["session_date"]=q40.target_ts.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
 refs=q30.assign(reference_mid=(q30.bid_price+q30.ask_price)/2)[["symbol","session_date","reference_mid"]]
 fills=q40[["symbol","session_date","bid_price","ask_price","quote_ts"]]
 trades=ledger[ledger.label.eq("0940")].merge(refs,on=["symbol","session_date"],how="left",validate="one_to_one").merge(fills,on=["symbol","session_date"],how="left",validate="one_to_one")
 trades["fill_price"]=np.where(trades.side.eq("buy"),trades.ask_price,trades.bid_price);trades["execution_drag"]=np.where(trades.side.eq("buy"),trades.fill_price/trades.reference_mid-1,1-trades.fill_price/trades.reference_mid);trades["commission_drag"]=trades.delta_weight*.0002;trades["cost"]=trades.delta_weight*trades.execution_drag+trades.commission_drag
 if trades[["reference_mid","fill_price","cost"]].isna().any().any():raise RuntimeError("incomplete quote replay")
 daily=pd.read_parquet(OUT/"gross_daily.parquet");daily.date=pd.to_datetime(daily.date);costs=trades.groupby("session_date").cost.sum();daily["quote_and_2bps_cost"]=daily.date.map(costs).fillna(0.0);daily["net_pnl"]=daily.gross_pnl-daily.quote_and_2bps_cost;daily["equity"]=1+daily.net_pnl.cumsum();peaks=np.maximum.accumulate(np.r_[1.0,daily.equity.to_numpy()])[1:];daily["drawdown"]=daily.equity/peaks-1
 monthly=daily.set_index("date").net_pnl.resample("ME").sum();weekly=daily.set_index("date").net_pnl.resample("W-FRI").sum()
 report={"status":"completed","authorized_window":{"start":"2026-08-01","end":"2026-08-10","maximum_loaded_date":str(daily.date.max().date()),"rows_after_authorized_end":0},"quote_coverage":1.0,"combined_net_fixed_base_pct":float(100*daily.net_pnl.sum()),"monthly_net_fixed_base_pct":{str(k.date()):float(100*v) for k,v in monthly.items()},"combined_max_drawdown_peak_relative_pct":float(-100*daily.drawdown.min()),"positive_days":int((daily.net_pnl>0).sum()),"negative_days":int((daily.net_pnl<0).sum()),"best_day_pct":float(100*daily.net_pnl.max()),"worst_day_pct":float(100*daily.net_pnl.min()),"weekly_pnl_pct":{str(k.date()):float(100*v) for k,v in weekly.items()},"turnover_weight":float(trades.delta_weight.sum()),"quote_execution_drag_pct":float(100*(trades.delta_weight*trades.execution_drag).sum()),"explicit_2bps_drag_pct":float(100*trades.commission_drag.sum())}
 trades.to_parquet(OUT/"quote_fill_ledger.parquet",index=False);daily.to_parquet(OUT/"oos_daily_net.parquet",index=False);(OUT/"oos_report.json").write_text(json.dumps(report,indent=2,default=str)+"\n");print(json.dumps(report,indent=2))

def benchmark():
 q=pd.read_parquet(OUT/"daily_all_adjusted_apr_augtd.parquet");q.date=pd.to_datetime(q.date);q=q[(q.symbol.eq("QQQ"))&q.date.between("2026-08-01","2026-08-10")].sort_values("date");report={"method":"first_session_open_to_last_session_close","august_to_date_return_pct":float(100*(q.close.iloc[-1]/q.open.iloc[0]-1)),"minimum_date":str(q.date.min().date()),"maximum_date":str(q.date.max().date()),"rows_after_authorized_end":0};(OUT/"qqq_benchmark.json").write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report,indent=2))

if __name__=="__main__":
 ap=argparse.ArgumentParser();ap.add_argument("phase",choices=("build","replay","benchmark"));a=ap.parse_args();globals()[a.phase]()
