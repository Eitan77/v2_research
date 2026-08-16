from __future__ import annotations

import argparse, json, sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]; SRC=ROOT/"campaigns"/"CAM-0600"/"src"; sys.path.insert(0,str(SRC)); sys.path.insert(0,str(Path(__file__).parent))
from baseline_strategies import eligible, moving_average
from deep_strategies import liquid_mask
from suite_core import evaluate_weights, load_panels, trailing_return, weekly_indices
from run_0027_rank_challengers import select_equal, period_metrics

OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0028"; CUTOFF=pd.Timestamp("2026-04-30"); HOLDOUT=pd.Timestamp("2026-05-01",tz="UTC"); NY=ZoneInfo("America/New_York")
CELLS=((1,0),(5,0),(5,1),(10,0),(10,1),(10,5),(20,0),(20,1),(20,5),(20,10),(30,0),(30,1),(30,5),(30,10),(126,21))

def vid(f,s): return f"f{f}_s{s}" if (f,s)!=(126,21) else "control_f126_s21"
def utc(day,clock):
 h,m=map(int,clock.split(":")); return pd.Timestamp(datetime.combine(pd.Timestamp(day).date(),time(h,m),tzinfo=NY)).tz_convert("UTC")
def build():
 p=load_panels()["qqq"]
 if p.dates.max()!=CUTOFF or p.readiness.get("holdout_rows_loaded_total",0)!=0: raise RuntimeError("QQQ readiness failed")
 signals=weekly_indices(p.dates); mask=eligible(p)&(moving_average(p,50)>moving_average(p,200))&liquid_mask(p,.5)
 weights={vid(f,s):select_equal(trailing_return(p,f,s),mask,signals) for f,s in CELLS}
 fixture={"status":"passed","planned_cells":15,"executed_cells":len(weights),"unique_ids":len(set(weights)),"maximum_gross":max(float(np.abs(w).sum(axis=1).max()) for w in weights.values()),"nonnegative":all(bool((w>=-1e-15).all()) for w in weights.values())}
 if fixture["executed_cells"]!=15 or fixture["unique_ids"]!=15 or fixture["maximum_gross"]>1+1e-12 or not fixture["nonnegative"]: raise RuntimeError(f"fixture failed {fixture}")
 return p,weights,fixture

def bars():
 OUT.mkdir(parents=True,exist_ok=True); p,weights,fixture=build(); split=p.dates[int(len(p.dates)*.60)]; rows=[]; ledgers={"0930":[],"0940":[]}
 for name,w in weights.items():
  f,s=next(x for x in CELLS if vid(*x)==name)
  for bps in (0.,1.,2.,5.,10.):
   met,daily,monthly,yearly,symbols=evaluate_weights(p,w,bps,holding="open_to_next_open",execution_lag=1); met.update({"variant":name,"formation":f,"skip":s,**period_metrics(daily.net_pnl,split)}); rows.append(met)
   if bps==2: daily.reset_index().to_parquet(OUT/f"bar_daily_{name}_2bps.parquet",index=False); symbols.reset_index().to_csv(OUT/f"bar_symbols_{name}_2bps.csv",index=False)
  executed=np.zeros_like(w); executed[1:]=w[:-1]; executed=np.where(np.isfinite(p.adj_open),executed,0); previous=np.zeros(p.n_symbols)
  for i,day in enumerate(p.dates):
   delta=executed[i]-previous
   for col in np.flatnonzero(np.abs(delta)>1e-12):
    side="buy" if delta[col]>0 else "sell"
    for label,clock in (("0930","09:30"),("0940","09:40")): ledgers[label].append({"variant":name,"session_date":pd.Timestamp(day).normalize(),"symbol":str(p.symbols[col]),"side":side,"delta_weight":float(abs(delta[col])),"target_ts":utc(day,clock),"role":"entry_ask_after" if side=="buy" else "exit_bid_after"})
   previous=executed[i].copy()
 metrics=pd.DataFrame(rows); metrics.to_json(OUT/"bar_metrics.json",orient="records",indent=2)
 profitable=set(metrics.loc[(metrics.cost_bps_per_side.isin([0.,1.,2.]))&(metrics.net_simple_return>0),"variant"])
 for label,records in ledgers.items():
  ledger=pd.DataFrame(records); ledger=ledger[ledger.variant.isin(profitable)].sort_values(["target_ts","variant","symbol"])
  if (pd.to_datetime(ledger.target_ts,utc=True)>=HOLDOUT).any(): raise RuntimeError("holdout role")
  ledger.to_parquet(OUT/f"ledger_{label}.parquet",index=False); ledger[["symbol","target_ts","role"]].drop_duplicates().to_parquet(OUT/f"roles_{label}.parquet",index=False)
 report={"status":"passed","fixture":fixture,"chronological_split":str(split.date()),"profitable_quote_candidates":sorted(profitable),"profitable_quote_candidate_count":len(profitable),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}; (OUT/"bar_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
 print(metrics[metrics.cost_bps_per_side.eq(2)][["variant","formation","skip","net_simple_return","maximum_drawdown","positive_months","negative_months","total_turnover","recent12_average_month","train","validation"]].sort_values("net_simple_return",ascending=False).to_string(index=False))

def quote_cache(label):
 frames=[]
 for priority,stem in enumerate((f"cached_quotes_{label}",f"quotes_{label}_5s",f"quotes_{label}_30s",f"quotes_{label}_1200s")):
  p=OUT/f"{stem}.parquet"
  if p.exists(): frames.append(pd.read_parquet(p).assign(priority=priority))
 q=pd.concat(frames,ignore_index=True); q.target_ts=pd.to_datetime(q.target_ts,utc=True); return q.sort_values("priority").drop_duplicates(["symbol","target_ts","role"],keep="first")

def replay():
 p,weights,fixture=build(); split=p.dates[int(len(p.dates)*.60)]; merged={}
 for label in ("0930","0940"):
  ledger=pd.read_parquet(OUT/f"ledger_{label}.parquet"); ledger.target_ts=pd.to_datetime(ledger.target_ts,utc=True); q=quote_cache(label)
  merged[label]=ledger.merge(q[["symbol","target_ts","role","quote_ts","bid_price","ask_price"]],on=["symbol","target_ts","role"],how="left",validate="many_to_one")
 ref=merged["0930"].copy(); ref["reference_mid"]=(ref.bid_price+ref.ask_price)/2; ref=ref[["variant","session_date","symbol","side","reference_mid"]]
 fills=merged["0940"].merge(ref,on=["variant","session_date","symbol","side"],how="left",validate="one_to_one")
 xmask=fills.symbol.eq("XLNX")&fills.session_date.eq(pd.Timestamp("2022-02-14"))&fills.side.eq("sell")
 if xmask.any():
  base=ROOT/"campaigns"/"CAM-0600"/"artifacts"/"RUN-0042"; xr=pd.read_parquet(base/"xlnx_reference_quote.parquet").iloc[0]; xt=pd.read_parquet(base/"xlnx_terminal_quote.parquet").iloc[0]
  fills.loc[xmask,"reference_mid"]=(float(xr.bid_price)+float(xr.ask_price))/2; fills.loc[xmask,"bid_price"]=float(xt.bid_price); fills.loc[xmask,"ask_price"]=float(xt.ask_price); fills.loc[xmask,"quote_ts"]=pd.Timestamp(xt.quote_ts); fills.loc[xmask,"session_date"]=pd.Timestamp("2022-02-11")
 fills["complete"]=fills.bid_price.notna()&fills.ask_price.notna()&fills.reference_mid.notna()&(fills.bid_price>0)&(fills.ask_price>=fills.bid_price)&(fills.reference_mid>0)
 if not fills.complete.all(): raise RuntimeError(f"incomplete roles {fills.loc[~fills.complete,['symbol','session_date']].to_dict('records')}")
 fills.to_parquet(OUT/"fill_ledger.parquet",index=False); rows=[]; monthly_rows=[]; symbol_rows=[]
 for name,w in weights.items():
  group=fills[fills.variant.eq(name)].copy(); _,daily,*_=evaluate_weights(p,w,0.,holding="open_to_next_open",execution_lag=1); executed=np.zeros_like(w); executed[1:]=w[:-1]; executed=np.where(np.isfinite(p.adj_open),executed,0); gross_symbol=executed*np.nan_to_num(p.open_to_next_open_return,nan=0); gross_symbol[-1]=executed[-1]*np.nan_to_num(p.open_to_close_return[-1],nan=0)
  f,s=next(x for x in CELLS if vid(*x)==name)
  for extra in (0.,1.,2.,5.,10.):
   cost=np.asarray(np.where(group.side.eq("buy"),group.delta_weight*(group.ask_price/group.reference_mid-1),group.delta_weight*(1-group.bid_price/group.reference_mid))+group.delta_weight.to_numpy(float)*extra/10000,dtype=float); cd=pd.Series(cost,index=pd.to_datetime(group.session_date)).groupby(level=0).sum(); net=daily.gross_pnl.subtract(cd,fill_value=0); eq=1+net.cumsum(); draw=(eq.cummax()-eq)/eq.cummax(); monthly=net.groupby(net.index.to_period("M")).sum(); recent=net.loc[net.index>=pd.Timestamp("2025-05-01")]; sm=pd.Series(cost,index=group.symbol).groupby(level=0).sum(); sg=pd.Series(gross_symbol.sum(axis=0),index=p.symbols.astype(str)); sn=sg.subtract(sm,fill_value=0).sort_values(ascending=False); pos=sn.clip(lower=0)
   rows.append({"variant":name,"formation":f,"skip":s,"extra_bps":extra,"net_simple_return":float(net.sum()),"maximum_drawdown":float(draw.max()),"positive_months":int((monthly>0).sum()),"negative_months":int((monthly<0).sum()),"worst_month":float(monthly.min()),"recent12_return":float(recent.sum()),"recent12_positive_months":int((recent.groupby(recent.index.to_period('M')).sum()>0).sum()),"turnover":float(group.delta_weight.sum()),"trade_sessions":int(pd.to_datetime(group.session_date).nunique()),"top5_symbol_positive_share":float(pos.head(5).sum()/pos.sum()),"leave_top5_return":float(net.sum()-sn.head(5).sum()),**period_metrics(net,split)})
   for m,pnl in monthly.items(): monthly_rows.append({"variant":name,"extra_bps":extra,"month":str(m),"net_pnl":float(pnl)})
   for sym,pnl in sn.items(): symbol_rows.append({"variant":name,"extra_bps":extra,"symbol":sym,"net_pnl":float(pnl)})
   pd.DataFrame({"date":net.index,"net_pnl":net.values}).to_parquet(OUT/f"quote_daily_{name}_{extra:g}bps.parquet",index=False)
 pd.DataFrame(rows).to_json(OUT/"quote_metrics.json",orient="records",indent=2); pd.DataFrame(monthly_rows).to_csv(OUT/"quote_monthly.csv",index=False); pd.DataFrame(symbol_rows).to_csv(OUT/"quote_symbols.csv",index=False)
 report={"status":"completed","fixture":fixture,"role_coverage":1.0,"ticker_exception":"XLNX last tradable session SIP exit","metrics":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin":False}; (OUT/"quote_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
 print(pd.DataFrame(rows).query("extra_bps==2")[["variant","formation","skip","net_simple_return","maximum_drawdown","positive_months","negative_months","recent12_return","recent12_positive_months","turnover","top5_symbol_positive_share","leave_top5_return","train","validation"]].sort_values("net_simple_return",ascending=False).to_string(index=False))

if __name__=="__main__":
 ap=argparse.ArgumentParser(); ap.add_argument("phase",choices=("bars","replay"),nargs="?",default="bars"); args=ap.parse_args(); {"bars":bars,"replay":replay}[args.phase]()
