from __future__ import annotations
import argparse,json,sys
from datetime import datetime,time
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"))
from baseline_strategies import eligible,moving_average
from deep_strategies import liquid_mask
from suite_core import evaluate_weights,forward_fill_signal_weights,load_panels,trailing_return,weekly_indices
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0032";NY=ZoneInfo("America/New_York");COST=9.740340417752536
IDS=("weekly_equal","weekly_uncapped","daily_equal","daily_uncapped")
def select(score,mask,sig,proportional):
 raw=np.zeros_like(score)
 for i in sig:
  cols=np.flatnonzero(mask[i]&np.isfinite(score[i]));
  if not len(cols):continue
  chosen=cols[np.argsort(score[i,cols],kind="stable")[-min(3,len(cols)):]][::-1]
  values=np.clip(score[i,chosen],0,None) if proportional else np.ones(len(chosen))
  if values.sum()<=0:values=np.ones(len(chosen))
  raw[i,chosen]=values/values.sum()
 return forward_fill_signal_weights(raw,sig)
def build():
 p=load_panels()["qqq"]
 if str(p.dates.max().date())!="2026-04-30" or p.readiness.get("holdout_rows_loaded_total",0)!=0:raise RuntimeError("readiness failed")
 score=trailing_return(p,126,21);mask=eligible(p)&(moving_average(p,50)>moving_average(p,200))&liquid_mask(p,.5);weekly=weekly_indices(p.dates);daily=np.arange(len(p.dates))
 w={"weekly_equal":select(score,mask,weekly,False),"weekly_uncapped":select(score,mask,weekly,True),"daily_equal":select(score,mask,daily,False),"daily_uncapped":select(score,mask,daily,True)}
 fixture={"status":"passed","variants":len(w),"maximum_loaded_date":str(p.dates.max().date()),"holdout_rows_loaded":0,"nonnegative":all(bool((x>=-1e-15).all()) for x in w.values()),"max_gross":max(float(x.sum(1).max()) for x in w.values()),"max_single_weight":{k:float(x.max()) for k,x in w.items()}}
 if fixture["variants"]!=4 or not fixture["nonnegative"] or fixture["max_gross"]>1+1e-12:raise RuntimeError(f"fixture failed {fixture}")
 return p,w,fixture
def summarize(net):
 eq=1+net.cumsum();mon=net.groupby(net.index.to_period("M")).sum();recent=net.loc[net.index>=pd.Timestamp("2025-05-01")]
 return {"net_simple_return":float(net.sum()),"maximum_drawdown":float(((eq.cummax()-eq)/eq.cummax()).max()),"positive_months":int((mon>0).sum()),"negative_months":int((mon<0).sum()),"worst_month":float(mon.min()),"recent12_return":float(recent.sum()),"recent12_positive_months":int((recent.groupby(recent.index.to_period('M')).sum()>0).sum())}
def bars():
 OUT.mkdir(parents=True,exist_ok=True);p,w,fixture=build();rows=[]
 for name,x in w.items():
  m,d,*_=evaluate_weights(p,x,COST,holding="open_to_next_open",execution_lag=1);rows.append({"variant":name,**summarize(d.net_pnl),"turnover":float(m["total_turnover"]),"trade_sessions":int((d.turnover>1e-12).sum())});np.save(OUT/f"weights_{name}.npy",x);d.reset_index().to_parquet(OUT/f"bar_daily_{name}.parquet",index=False)
 (OUT/"bar_report.json").write_text(json.dumps({"status":"completed_bar_stage","fixture":fixture,"metrics":rows},indent=2)+"\n");print(pd.DataFrame(rows).to_string(index=False))
def ledgers():
 p,w,_=build();records={"0930":[],"0940":[]}
 for name,x in w.items():
  if "uncapped" not in name:continue
  exe=np.zeros_like(x);exe[1:]=x[:-1];exe=np.where(np.isfinite(p.adj_open),exe,0);prev=np.zeros(p.n_symbols)
  for i,day in enumerate(p.dates):
   delta=exe[i]-prev
   for col in np.flatnonzero(np.abs(delta)>1e-12):
    side="buy" if delta[col]>0 else "sell"
    for label,clock in (("0930",(9,30)),("0940",(9,40))):records[label].append({"variant":name,"session_date":pd.Timestamp(day).normalize(),"symbol":str(p.symbols[col]),"side":side,"delta_weight":float(abs(delta[col])),"target_ts":pd.Timestamp(datetime.combine(pd.Timestamp(day).date(),time(*clock),tzinfo=NY)).tz_convert("UTC"),"role":"entry_ask_after" if side=="buy" else "exit_bid_after"})
   prev=exe[i].copy()
 for label,rows in records.items():
  q=pd.DataFrame(rows).sort_values(["target_ts","variant","symbol"]);q.to_parquet(OUT/f"ledger_{label}.parquet",index=False);q[["symbol","target_ts","role"]].drop_duplicates().to_parquet(OUT/f"roles_{label}.parquet",index=False)
 print({k:len(v) for k,v in records.items()})
def cache_existing(label):
 frames=[]
 for run in ("RUN-0030","RUN-0031"):
  base=ROOT/"campaigns"/"CAM-0611"/"artifacts"/run
  for stem in (f"cached_quotes_{label}",f"quotes_{label}_5s",f"quotes_{label}_30s",f"quotes_{label}_1200s"):
   path=base/f"{stem}.parquet"
   if path.exists():frames.append(pd.read_parquet(path))
 for seconds in (5,30,1200):
  path=OUT/f"quotes_{label}_{seconds}s.parquet"
  if path.exists():frames.append(pd.read_parquet(path))
 q=pd.concat(frames,ignore_index=True);q.target_ts=pd.to_datetime(q.target_ts,utc=True);q.quote_ts=pd.to_datetime(q.quote_ts,utc=True);return q.sort_values("quote_ts").drop_duplicates(["symbol","target_ts","role"])
def prepare_missing():
 for label in ("0930","0940"):
  roles=pd.read_parquet(OUT/f"roles_{label}.parquet");roles.target_ts=pd.to_datetime(roles.target_ts,utc=True);q=cache_existing(label);m=roles.merge(q[["symbol","target_ts","role"]],on=["symbol","target_ts","role"],how="left",indicator=True);missing=m[m._merge.eq("left_only")][["symbol","target_ts","role"]];missing.to_parquet(OUT/f"missing_{label}_5s_roles.parquet",index=False);print(label,len(roles),len(missing))
def replay():
 p,w,fixture=build();merged={};keys=["symbol","target_ts","role"]
 for label in ("0930","0940"):
  l=pd.read_parquet(OUT/f"ledger_{label}.parquet");l.target_ts=pd.to_datetime(l.target_ts,utc=True);q=cache_existing(label);merged[label]=l.merge(q[keys+["quote_ts","bid_price","ask_price"]],on=keys,how="left",validate="many_to_one")
 ref=merged["0930"].copy();ref["reference_mid"]=(ref.bid_price+ref.ask_price)/2;ref=ref[["variant","session_date","symbol","side","reference_mid"]];fills=merged["0940"].merge(ref,on=["variant","session_date","symbol","side"],how="left",validate="one_to_one")
 x=fills.symbol.eq("XLNX")&fills.session_date.eq(pd.Timestamp("2022-02-14"))&fills.side.eq("sell")
 if x.any():
  base=ROOT/"campaigns"/"CAM-0600"/"artifacts"/"RUN-0042";a=pd.read_parquet(base/"xlnx_reference_quote.parquet").iloc[0];b=pd.read_parquet(base/"xlnx_terminal_quote.parquet").iloc[0];fills.loc[x,"reference_mid"]=(float(a.bid_price)+float(a.ask_price))/2;fills.loc[x,"bid_price"]=float(b.bid_price);fills.loc[x,"ask_price"]=float(b.ask_price);fills.loc[x,"session_date"]=pd.Timestamp("2022-02-11")
 complete=fills.bid_price.notna()&fills.ask_price.notna()&fills.reference_mid.notna()&(fills.bid_price>0)&(fills.ask_price>=fills.bid_price)&(fills.reference_mid>0)
 if not complete.all():raise RuntimeError(f"missing {int((~complete).sum())} quote roles")
 fills.to_parquet(OUT/"fill_ledger.parquet",index=False);rows=[]
 for name in ("weekly_uncapped","daily_uncapped"):
  g=fills[fills.variant.eq(name)];_,d,*_=evaluate_weights(p,w[name],0,holding="open_to_next_open",execution_lag=1)
  for extra in (0.,1.,2.,5.,10.):
   cost=np.where(g.side.eq("buy"),g.delta_weight*(g.ask_price/g.reference_mid-1),g.delta_weight*(1-g.bid_price/g.reference_mid))+g.delta_weight.to_numpy()*extra/10000;cd=pd.Series(cost,index=pd.to_datetime(g.session_date)).groupby(level=0).sum();net=d.gross_pnl.subtract(cd,fill_value=0);rows.append({"variant":name,"extra_bps":extra,**summarize(net),"turnover":float(g.delta_weight.sum()),"trade_roles":len(g),"trade_sessions":int(pd.to_datetime(g.session_date).nunique()),"role_coverage":1.0});pd.DataFrame({"date":net.index,"net_pnl":net.values}).to_parquet(OUT/f"quote_daily_{name}_{extra:g}bps.parquet",index=False)
 report={"status":"completed","fixture":fixture,"metrics":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin":False};(OUT/"quote_report.json").write_text(json.dumps(report,indent=2)+"\n");print(pd.DataFrame(rows).query("extra_bps==2").to_string(index=False))
if __name__=="__main__":
 ap=argparse.ArgumentParser();ap.add_argument("phase",choices=("bars","ledgers","prepare_missing","replay"),nargs="?",default="bars");a=ap.parse_args();globals()[a.phase]()
