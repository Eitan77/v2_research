from __future__ import annotations
import argparse,json,sys
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import duckdb
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"))
from baseline_strategies import eligible
from suite_core import load_panels,trailing_return,weekly_indices
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0039";NY=ZoneInfo("America/New_York");KEY=["symbol","target_ts","role"]
def context():
 p=load_panels()["qqq"]
 if str(p.dates.max().date())!="2026-04-30" or p.readiness.get("holdout_rows_loaded_total",0)!=0:raise RuntimeError("readiness")
 sig=weekly_indices(p.dates);con=duckdb.connect(r"D:\AlgoResearch\data\catalog.duckdb",read_only=True);cal=con.execute("select try_cast(date as date) as session_date, arg_max(close, try_cast(ingested_at as timestamp)) as close_time from calendar where try_cast(date as date)<=date '2026-04-30' group by 1").fetchdf();cal.session_date=pd.to_datetime(cal.session_date);cl=dict(zip(cal.session_date.dt.normalize(),cal.close_time));return p,sig,cl
def target(day,close_text,minutes):
 h,m=map(int,str(close_text).split(":"));return pd.Timestamp(datetime.combine(pd.Timestamp(day).date(),datetime.min.time().replace(hour=h,minute=m),tzinfo=NY)-timedelta(minutes=minutes)).tz_convert("UTC")
def signal_roles():
 OUT.mkdir(parents=True,exist_ok=True);p,sig,cl=context();rows=[]
 for i in sig:
  day=pd.Timestamp(p.dates[i]).normalize();close=cl.get(day,"16:00");ts=target(day,close,10)
  for c in np.flatnonzero(p.member[i]&np.isfinite(p.raw_close[i])):rows.append({"symbol":str(p.symbols[c]),"target_ts":ts,"role":"entry_ask_after"})
 x=pd.DataFrame(rows).drop_duplicates(KEY).sort_values(["target_ts","symbol"]);x.to_parquet(OUT/"signal_roles.parquet",index=False);print({"roles":len(x),"dates":x.target_ts.nunique(),"symbols":x.symbol.nunique()})
def quote_cache(prefix):
 frames=[]
 for seconds in (5,30,1200):
  p=OUT/f"{prefix}_{seconds}s.parquet"
  if p.exists():frames.append(pd.read_parquet(p))
 q=pd.concat(frames,ignore_index=True);q.target_ts=pd.to_datetime(q.target_ts,utc=True);q.quote_ts=pd.to_datetime(q.quote_ts,utc=True);return q.sort_values("quote_ts").drop_duplicates(KEY)
def missing(prefix,roles_name):
 r=pd.read_parquet(OUT/roles_name);r.target_ts=pd.to_datetime(r.target_ts,utc=True);q=quote_cache(prefix);m=r.merge(q[KEY],on=KEY,how="left",indicator=True);z=m[m._merge.eq("left_only")][KEY];z.to_parquet(OUT/f"{prefix}_missing.parquet",index=False);print({"roles":len(r),"missing":len(z)})
def build():
 p,sig,cl=context();q=quote_cache("signal_quotes");r=pd.read_parquet(OUT/"signal_roles.parquet");r.target_ts=pd.to_datetime(r.target_ts,utc=True);x=r.merge(q[KEY+["bid_price","ask_price","quote_ts"]],on=KEY,how="left",validate="one_to_one");x["mid"]=(x.bid_price+x.ask_price)/2;lookup={(pd.Timestamp(z.target_ts).tz_convert(NY).date(),z.symbol):z.mid for z in x.itertuples() if np.isfinite(z.mid)};mom=trailing_return(p,126,21);dv=pd.DataFrame(p.raw_close*p.volume).shift(1).rolling(63,min_periods=32).median().to_numpy();prior49=pd.DataFrame(p.adj_close).shift(1).rolling(49,min_periods=49).sum().to_numpy();prior199=pd.DataFrame(p.adj_close).shift(1).rolling(199,min_periods=199).sum().to_numpy();weights=np.zeros((len(p.dates),p.n_symbols));attr=[]
 for i in sig:
  day=pd.Timestamp(p.dates[i]);mid=np.full(p.n_symbols,np.nan)
  for c,s in enumerate(p.symbols.astype(str)):mid[c]=lookup.get((day.date(),s),np.nan)
  partial=mid*p.split_factor[i];s50=(prior49[i]+partial)/50;s200=(prior199[i]+partial)/200;ready=eligible(p)[i]&np.isfinite(mid)&np.isfinite(s50)&np.isfinite(s200)&(s50>s200)&np.isfinite(mom[i])&np.isfinite(dv[i]);cols=np.flatnonzero(ready);n=max(1,int(np.ceil(len(cols)*.5))) if len(cols) else 0;liq=cols[np.argsort(dv[i,cols],kind="stable")[-n:]] if n else np.array([],int);chosen=liq[np.argsort(mom[i,liq],kind="stable")[-min(3,len(liq)):]] if len(liq) else np.array([],int)
  if len(chosen):weights[i,chosen]=1/len(chosen)
  attr.append({"date":str(day.date()),"pit_members":int(p.member[i].sum()),"signal_quotes":int(np.isfinite(mid).sum()),"eligible_before_liquidity":len(cols),"eligible_after_liquidity":len(liq),"selected":len(chosen)})
 # forward fill only between weekly signals
 last=np.zeros(p.n_symbols);ss=set(sig.tolist())
 for i in range(len(weights)):
  if i in ss:last=weights[i].copy()
  weights[i]=last
 np.save(OUT/"weights.npy",weights);pd.DataFrame(attr).to_csv(OUT/"signal_attrition.csv",index=False)
 # Execution roles only for target changes at close minus five minutes.
 rows=[];marks=[];prev=np.zeros(p.n_symbols)
 for i in sig:
  day=pd.Timestamp(p.dates[i]).normalize();delta=weights[i]-prev;ts=target(day,cl.get(day,"16:00"),5)
  for c in np.flatnonzero((weights[i]>1e-12)|(prev>1e-12)):marks.append({"symbol":str(p.symbols[c]),"target_ts":ts,"role":"mark_after"})
  for c in np.flatnonzero(np.abs(delta)>1e-12):rows.append({"session_date":day,"symbol":str(p.symbols[c]),"side":"buy" if delta[c]>0 else "sell","delta_weight":float(abs(delta[c])),"target_ts":ts,"role":"entry_ask_after" if delta[c]>0 else "exit_bid_after"})
  prev=weights[i].copy()
 e=pd.DataFrame(rows).sort_values(["target_ts","symbol"]);e.to_parquet(OUT/"execution_ledger.parquet",index=False);roles=pd.concat([e[KEY],pd.DataFrame(marks)],ignore_index=True).drop_duplicates(KEY);roles.to_parquet(OUT/"execution_roles.parquet",index=False);print({"signal_coverage":float(x.mid.notna().mean()),"execution_trade_roles":len(e),"execution_all_roles":len(roles),"active_weeks":int((weights[sig].sum(1)>0).sum())})
def replay():
 p,sig,cl=context();weights=np.load(OUT/"weights.npy");quotes=quote_cache("execution_quotes");signal_roles=pd.read_parquet(OUT/"signal_roles.parquet");signal_quotes=quote_cache("signal_quotes");signal_cov=len(signal_roles.merge(signal_quotes[KEY],on=KEY,how="inner"))/len(signal_roles);marks=quotes[quotes.role.eq("mark_after")].copy();marks["mid"]=(marks.bid_price+marks.ask_price)/2;marks["session_date"]=marks.target_ts.dt.tz_convert(NY).dt.tz_localize(None).dt.normalize();mark_lookup={(z.session_date,z.symbol):z.mid for z in marks.itertuples()};ledger=pd.read_parquet(OUT/"execution_ledger.parquet");ledger.target_ts=pd.to_datetime(ledger.target_ts,utc=True);ledger.session_date=pd.to_datetime(ledger.session_date);ledger=ledger.merge(quotes[KEY+["bid_price","ask_price"]],on=KEY,how="left",validate="one_to_one");ledger["mark_mid"]=[mark_lookup.get((pd.Timestamp(d),s),np.nan) for d,s in zip(ledger.session_date,ledger.symbol)]
 if ledger[["bid_price","ask_price","mark_mid"]].isna().any().any():raise RuntimeError("execution quote coverage failed")
 signal_set=set(sig.tolist());base_gross=np.zeros(len(p.dates));current=np.zeros(p.n_symbols)
 for i,day in enumerate(p.dates):
  if i in signal_set:
   mark=np.full(p.n_symbols,np.nan)
   for c,s in enumerate(p.symbols.astype(str)):mark[c]=mark_lookup.get((pd.Timestamp(day).normalize(),s),np.nan)
   adjmark=mark*p.split_factor[i];new=weights[i].copy()
   if i>0:
    dividend=np.nan_to_num(p.dividend_grid[i]*p.split_factor[i],nan=0);pre=np.divide(adjmark+dividend,p.adj_close[i-1],out=np.zeros(p.n_symbols),where=np.isfinite(adjmark)&np.isfinite(p.adj_close[i-1])&(p.adj_close[i-1]>0))-1;pre=np.where(np.isfinite(adjmark),pre,0);base_gross[i]+=float(np.sum(current*pre))
   post=np.divide(p.adj_close[i],adjmark,out=np.ones(p.n_symbols),where=np.isfinite(adjmark)&(adjmark>0)&np.isfinite(p.adj_close[i]))-1;base_gross[i]+=float(np.sum(new*post));current=new
  elif i>0:
   step=np.divide(p.total_return_index[i],p.total_return_index[i-1],out=np.ones(p.n_symbols),where=np.isfinite(p.total_return_index[i-1])&(p.total_return_index[i-1]>0))-1;base_gross[i]=float(np.sum(current*step))
 daily_index=pd.DatetimeIndex(p.dates);rows=[]
 for extra in (0.,1.,2.,5.,10.):
  cost=np.where(ledger.side.eq("buy"),ledger.delta_weight*(ledger.ask_price/ledger.mark_mid-1),ledger.delta_weight*(1-ledger.bid_price/ledger.mark_mid))+ledger.delta_weight.to_numpy()*extra/10000;cd=pd.Series(cost,index=ledger.session_date).groupby(level=0).sum();net=pd.Series(base_gross,index=daily_index).subtract(cd,fill_value=0);eq=1+net.cumsum();dd=(eq.cummax()-eq)/eq.cummax();mon=net.groupby(net.index.to_period("M")).sum();yr=net.groupby(net.index.year).sum();recent=net[net.index>=pd.Timestamp("2025-05-01")];rows.append({"extra_bps":extra,"net_simple_return":float(net.sum()),"maximum_drawdown":float(dd.max()),"positive_months":int((mon>0).sum()),"negative_months":int((mon<0).sum()),"worst_month":float(mon.min()),"worst_year":float(yr.min()),"recent12_return":float(recent.sum()),"recent12_positive_months":int((recent.groupby(recent.index.to_period('M')).sum()>0).sum()),"turnover":float(ledger.delta_weight.sum()),"trade_roles":len(ledger),"signal_quote_coverage":signal_cov,"execution_role_coverage":1.0});pd.DataFrame({"date":net.index,"net_pnl":net.values}).to_parquet(OUT/f"quote_daily_{extra:g}bps.parquet",index=False)
 report={"status":"completed","metrics":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin":False};(OUT/"quote_report.json").write_text(json.dumps(report,indent=2)+"\n");print(pd.DataFrame(rows).to_string(index=False))
if __name__=="__main__":
 ap=argparse.ArgumentParser();ap.add_argument("phase",choices=("signal_roles","build","replay"));a=ap.parse_args();globals()[a.phase]()
