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
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0031";COST=9.740340417752536;NY=ZoneInfo("America/New_York")
IDS=("equal_control","rank_50_30_20","score_proportional_cap50","hybrid_equal_score")
def capped(values,cap=.5):
 values=np.clip(np.asarray(values,float),0,None)
 if values.sum()<=0:values=np.ones(len(values))
 w=values/values.sum();fixed=np.zeros(len(w),bool)
 while np.any(w>cap+1e-15):
  hit=(w>cap)&~fixed;w[hit]=cap;fixed|=hit;remaining=1-w[fixed].sum();free=~fixed
  if not free.any():break
  base=values[free];w[free]=remaining*(base/base.sum() if base.sum()>0 else np.ones(free.sum())/free.sum())
 return w/w.sum()
def build():
 p=load_panels()["qqq"]
 if str(p.dates.max().date())!="2026-04-30" or p.readiness.get("holdout_rows_loaded_total",0)!=0:raise RuntimeError("readiness failed")
 sig=weekly_indices(p.dates);score=trailing_return(p,126,21);mask=eligible(p)&(moving_average(p,50)>moving_average(p,200))&liquid_mask(p,.5);raw={k:np.zeros_like(score) for k in IDS}
 for i in sig:
  cols=np.flatnonzero(mask[i]&np.isfinite(score[i]));
  if not len(cols):continue
  chosen=cols[np.argsort(score[i,cols],kind="stable")[-min(3,len(cols)):]][::-1];n=len(chosen);eq=np.ones(n)/n;rank=np.array([.5,.3,.2])[:n];rank/=rank.sum();prop=capped(np.clip(score[i,chosen],0,None),.5);hybrid=.5*eq+.5*prop
  for name,w in (("equal_control",eq),("rank_50_30_20",rank),("score_proportional_cap50",prop),("hybrid_equal_score",hybrid)):raw[name][i,chosen]=w
 weights={k:forward_fill_signal_weights(v,sig) for k,v in raw.items()};fixture={"status":"passed","variants":len(weights),"max_gross":max(float(w.sum(axis=1).max()) for w in weights.values()),"nonnegative":all(bool((w>=-1e-15).all()) for w in weights.values()),"score_cap":float(weights["score_proportional_cap50"].max())}
 if fixture["variants"]!=4 or fixture["max_gross"]>1+1e-12 or not fixture["nonnegative"] or fixture["score_cap"]>.5+1e-12:raise RuntimeError(f"fixture {fixture}")
 return p,weights,fixture
def bars():
 OUT.mkdir(parents=True,exist_ok=True);p,weights,fixture=build();split=p.dates[int(len(p.dates)*.6)];rows=[]
 for name,w in weights.items():
  m,d,*rest=evaluate_weights(p,w,COST,holding="open_to_next_open",execution_lag=1);recent=d.net_pnl.loc[d.index>=pd.Timestamp("2025-05-01")];train=d.net_pnl.loc[d.index<=split];valid=d.net_pnl.loc[d.index>split]
  def pm(x):
   eq=1+x.cumsum();mon=x.groupby(x.index.to_period('M')).sum();return {"net":float(x.sum()),"dd":float(((eq.cummax()-eq)/eq.cummax()).max()),"positive_months":int((mon>0).sum()),"negative_months":int((mon<0).sum())}
  rows.append({"variant":name,"net_simple_return":float(d.net_pnl.sum()),"maximum_drawdown":float(m["maximum_drawdown"]),"turnover":float(m["total_turnover"]),"positive_months":int(m["positive_months"]),"negative_months":int(m["negative_months"]),"recent12_return":float(recent.sum()),"recent12_positive_months":int((recent.groupby(recent.index.to_period('M')).sum()>0).sum()),"top5_symbol_positive_share":m["top5_symbol_positive_share"],"leave_best_symbol_out_return":m["leave_best_symbol_out_return"],"train":pm(train),"validation":pm(valid)})
  d.reset_index().to_parquet(OUT/f"bar_daily_{name}.parquet",index=False);np.save(OUT/f"weights_{name}.npy",w)
 report={"status":"completed_bar_stage","fixture":fixture,"metrics":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0};(OUT/"bar_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8");print(pd.DataFrame(rows).to_string(index=False))
def ledgers():
 p,weights,_=build();records={"0930":[],"0940":[]}
 for name,w in weights.items():
  exe=np.zeros_like(w);exe[1:]=w[:-1];exe=np.where(np.isfinite(p.adj_open),exe,0);prev=np.zeros(p.n_symbols)
  for i,day in enumerate(p.dates):
   delta=exe[i]-prev
   for col in np.flatnonzero(np.abs(delta)>1e-12):
    side="buy" if delta[col]>0 else "sell"
    for label,clock in (("0930",(9,30)),("0940",(9,40))):records[label].append({"variant":name,"session_date":pd.Timestamp(day).normalize(),"symbol":str(p.symbols[col]),"side":side,"delta_weight":float(abs(delta[col])),"target_ts":pd.Timestamp(datetime.combine(pd.Timestamp(day).date(),time(*clock),tzinfo=NY)).tz_convert("UTC"),"role":"entry_ask_after" if side=="buy" else "exit_bid_after"})
   prev=exe[i].copy()
 for label,rows in records.items():
  l=pd.DataFrame(rows).sort_values(["target_ts","variant","symbol"]);l.to_parquet(OUT/f"ledger_{label}.parquet",index=False);l[["symbol","target_ts","role"]].drop_duplicates().to_parquet(OUT/f"roles_{label}.parquet",index=False)
 print({k:len(v) for k,v in records.items()})
def replay():
 p,weights,fixture=build();keys=["symbol","target_ts","role"];merged={}
 for label in ("0930","0940"):
  l=pd.read_parquet(OUT/f"ledger_{label}.parquet");l.target_ts=pd.to_datetime(l.target_ts,utc=True);frames=[]
  for stem in (f"cached_quotes_{label}",f"quotes_{label}_5s",f"quotes_{label}_30s"):
   path=OUT/f"{stem}.parquet"
   if path.exists():frames.append(pd.read_parquet(path))
  q=pd.concat(frames,ignore_index=True);q.target_ts=pd.to_datetime(q.target_ts,utc=True);q.quote_ts=pd.to_datetime(q.quote_ts,utc=True);q=q.sort_values("quote_ts").drop_duplicates(keys);merged[label]=l.merge(q[keys+["quote_ts","bid_price","ask_price"]],on=keys,how="left",validate="many_to_one")
 ref=merged["0930"].copy();ref["reference_mid"]=(ref.bid_price+ref.ask_price)/2;ref=ref[["variant","session_date","symbol","side","reference_mid"]];fills=merged["0940"].merge(ref,on=["variant","session_date","symbol","side"],how="left",validate="one_to_one")
 x=fills.symbol.eq("XLNX")&fills.session_date.eq(pd.Timestamp("2022-02-14"))&fills.side.eq("sell")
 if x.any():
  base=ROOT/"campaigns"/"CAM-0600"/"artifacts"/"RUN-0042";xr=pd.read_parquet(base/"xlnx_reference_quote.parquet").iloc[0];xt=pd.read_parquet(base/"xlnx_terminal_quote.parquet").iloc[0];fills.loc[x,"reference_mid"]=(float(xr.bid_price)+float(xr.ask_price))/2;fills.loc[x,"bid_price"]=float(xt.bid_price);fills.loc[x,"ask_price"]=float(xt.ask_price);fills.loc[x,"session_date"]=pd.Timestamp("2022-02-11")
 fills["complete"]=fills.bid_price.notna()&fills.ask_price.notna()&fills.reference_mid.notna()&(fills.bid_price>0)&(fills.ask_price>=fills.bid_price)&(fills.reference_mid>0)
 if not fills.complete.all():raise RuntimeError(f"missing {fills.loc[~fills.complete,['symbol','session_date']].to_dict('records')}")
 fills.to_parquet(OUT/"fill_ledger.parquet",index=False);rows=[];split=p.dates[int(len(p.dates)*.6)]
 for name,w in weights.items():
  g=fills[fills.variant.eq(name)].copy();_,daily,*_=evaluate_weights(p,w,0.,holding="open_to_next_open",execution_lag=1)
  for extra in (0.,1.,2.,5.,10.):
   cost=np.asarray(np.where(g.side.eq("buy"),g.delta_weight*(g.ask_price/g.reference_mid-1),g.delta_weight*(1-g.bid_price/g.reference_mid))+g.delta_weight.to_numpy(float)*extra/10000,dtype=float);cd=pd.Series(cost,index=pd.to_datetime(g.session_date)).groupby(level=0).sum();net=daily.gross_pnl.subtract(cd,fill_value=0);eq=1+net.cumsum();dd=((eq.cummax()-eq)/eq.cummax()).max();mon=net.groupby(net.index.to_period('M')).sum();recent=net.loc[net.index>=pd.Timestamp("2025-05-01")];valid=net.loc[net.index>split]
   rows.append({"variant":name,"extra_bps":extra,"net_simple_return":float(net.sum()),"maximum_drawdown":float(dd),"positive_months":int((mon>0).sum()),"negative_months":int((mon<0).sum()),"worst_month":float(mon.min()),"recent12_return":float(recent.sum()),"recent12_positive_months":int((recent.groupby(recent.index.to_period('M')).sum()>0).sum()),"validation_return":float(valid.sum()),"turnover":float(g.delta_weight.sum()),"trade_roles":len(g),"role_coverage":1.0})
 report={"status":"completed","fixture":fixture,"metrics":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin":False};(OUT/"quote_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8");pd.DataFrame(rows).to_json(OUT/"quote_metrics.json",orient="records",indent=2);print(pd.DataFrame(rows).query("extra_bps==2").to_string(index=False))
if __name__=="__main__":
 ap=argparse.ArgumentParser();ap.add_argument("phase",choices=("bars","ledgers","replay"),nargs="?",default="bars");a=ap.parse_args();{"bars":bars,"ledgers":ledgers,"replay":replay}[a.phase]()
