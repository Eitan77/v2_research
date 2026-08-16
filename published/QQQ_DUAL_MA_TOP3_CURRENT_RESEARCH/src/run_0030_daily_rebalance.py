from __future__ import annotations
import argparse,json,sys
from datetime import datetime,time
from zoneinfo import ZoneInfo
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src")); sys.path.insert(0,str(Path(__file__).parent))
from baseline_strategies import eligible,moving_average
from deep_strategies import liquid_mask
from suite_core import evaluate_weights,load_panels,trailing_return,weekly_indices
from run_0027_rank_challengers import select_equal
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0030"; COST=9.740340417752536
def main():
 OUT.mkdir(parents=True,exist_ok=True); p=load_panels()["qqq"]
 if str(p.dates.max().date())!="2026-04-30" or p.readiness.get("holdout_rows_loaded_total",0)!=0: raise RuntimeError("readiness failed")
 mask=eligible(p)&(moving_average(p,50)>moving_average(p,200))&liquid_mask(p,.5); score=trailing_return(p,126,21)
 signals={"weekly_control":weekly_indices(p.dates),"daily_rebalance":np.arange(len(p.dates),dtype=int)}; rows=[]
 for name,sig in signals.items():
  w=select_equal(score,mask,sig,3); m,d,monthly,yearly,symbols=evaluate_weights(p,w,COST,holding="open_to_next_open",execution_lag=1); recent=d.net_pnl.loc[d.index>=pd.Timestamp("2025-05-01")]; weeks=d.net_pnl.groupby(d.index.to_period("W-FRI")).sum()
  rows.append({"variant":name,"net_simple_return":float(d.net_pnl.sum()),"maximum_drawdown":float(m["maximum_drawdown"]),"turnover":float(m["total_turnover"]),"position_changes":int(m["position_change_count"]),"trade_sessions":int((d.turnover>1e-12).sum()),"positive_months":int(m["positive_months"]),"negative_months":int(m["negative_months"]),"recent12_return":float(recent.sum()),"recent12_positive_months":int((recent.groupby(recent.index.to_period('M')).sum()>0).sum()),"average_week_profit":float(weeks.mean()),"top5_symbol_positive_share":m["top5_symbol_positive_share"],"leave_best_symbol_out_return":m["leave_best_symbol_out_return"]})
  d.reset_index().to_parquet(OUT/f"daily_{name}.parquet",index=False); symbols.reset_index().to_csv(OUT/f"symbols_{name}.csv",index=False); np.save(OUT/f"weights_{name}.npy",w)
 report={"status":"completed_bar_stage","execution_model":"bar plus frozen average quote slippage plus 2 bp adverse","metrics":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}; (OUT/"bar_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); print(pd.DataFrame(rows).to_string(index=False))
def ledgers():
 p=load_panels()["qqq"]; w=np.load(OUT/"weights_daily_rebalance.npy"); executed=np.zeros_like(w); executed[1:]=w[:-1]; executed=np.where(np.isfinite(p.adj_open),executed,0); previous=np.zeros(p.n_symbols); ny=ZoneInfo("America/New_York"); records={"0930":[],"0940":[]}
 for i,day in enumerate(p.dates):
  delta=executed[i]-previous
  for col in np.flatnonzero(np.abs(delta)>1e-12):
   side="buy" if delta[col]>0 else "sell"
   for label,clock in (("0930",(9,30)),("0940",(9,40))): records[label].append({"session_date":pd.Timestamp(day).normalize(),"symbol":str(p.symbols[col]),"side":side,"delta_weight":float(abs(delta[col])),"target_ts":pd.Timestamp(datetime.combine(pd.Timestamp(day).date(),time(*clock),tzinfo=ny)).tz_convert("UTC"),"role":"entry_ask_after" if side=="buy" else "exit_bid_after"})
  previous=executed[i].copy()
 for label,rows in records.items():
  ledger=pd.DataFrame(rows).sort_values(["target_ts","symbol"]); ledger.to_parquet(OUT/f"ledger_{label}.parquet",index=False); ledger[["symbol","target_ts","role"]].drop_duplicates().to_parquet(OUT/f"roles_{label}.parquet",index=False)
 print({k:len(v) for k,v in records.items()})
def replay():
 p=load_panels()["qqq"];w=np.load(OUT/"weights_daily_rebalance.npy");merged={};keys=["symbol","target_ts","role"]
 for label in ("0930","0940"):
  ledger=pd.read_parquet(OUT/f"ledger_{label}.parquet");ledger.target_ts=pd.to_datetime(ledger.target_ts,utc=True);frames=[]
  for stem in (f"cached_quotes_{label}",f"quotes_{label}_5s",f"quotes_{label}_30s",f"quotes_{label}_1200s"):
   path=OUT/f"{stem}.parquet"
   if path.exists():frames.append(pd.read_parquet(path))
  q=pd.concat(frames,ignore_index=True);q.target_ts=pd.to_datetime(q.target_ts,utc=True);q.quote_ts=pd.to_datetime(q.quote_ts,utc=True);q=q.sort_values("quote_ts").drop_duplicates(keys,keep="first");merged[label]=ledger.merge(q[keys+["quote_ts","bid_price","ask_price"]],on=keys,how="left",validate="one_to_one")
 ref=merged["0930"].copy();ref["reference_mid"]=(ref.bid_price+ref.ask_price)/2;ref=ref[["session_date","symbol","side","reference_mid"]];fills=merged["0940"].merge(ref,on=["session_date","symbol","side"],how="left",validate="one_to_one");fills["complete"]=fills.bid_price.notna()&fills.ask_price.notna()&fills.reference_mid.notna()&(fills.bid_price>0)&(fills.ask_price>=fills.bid_price)&(fills.reference_mid>0)
 if not fills.complete.all():raise RuntimeError(f"missing {fills.loc[~fills.complete,['symbol','session_date']].to_dict('records')}")
 fills.to_parquet(OUT/"fill_ledger.parquet",index=False);_,daily,*_=evaluate_weights(p,w,0.,holding="open_to_next_open",execution_lag=1);rows=[]
 for extra in (0.,1.,2.,5.,10.):
  cost=np.asarray(np.where(fills.side.eq("buy"),fills.delta_weight*(fills.ask_price/fills.reference_mid-1),fills.delta_weight*(1-fills.bid_price/fills.reference_mid))+fills.delta_weight.to_numpy(float)*extra/10000,dtype=float);cd=pd.Series(cost,index=pd.to_datetime(fills.session_date)).groupby(level=0).sum();net=daily.gross_pnl.subtract(cd,fill_value=0);eq=1+net.cumsum();dd=((eq.cummax()-eq)/eq.cummax()).max();monthly=net.groupby(net.index.to_period("M")).sum();recent=net.loc[net.index>=pd.Timestamp("2025-05-01")];rows.append({"extra_bps":extra,"net_simple_return":float(net.sum()),"maximum_drawdown":float(dd),"positive_months":int((monthly>0).sum()),"negative_months":int((monthly<0).sum()),"worst_month":float(monthly.min()),"recent12_return":float(recent.sum()),"recent12_positive_months":int((recent.groupby(recent.index.to_period('M')).sum()>0).sum()),"trade_roles":len(fills),"trade_sessions":int(pd.to_datetime(fills.session_date).nunique()),"role_coverage":1.0})
  pd.DataFrame({"date":net.index,"net_pnl":net.values}).to_parquet(OUT/f"quote_daily_{extra:g}bps.parquet",index=False)
 report={"status":"completed","metrics":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin":False};(OUT/"quote_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8");print(pd.DataFrame(rows).to_string(index=False))
if __name__=="__main__":
 ap=argparse.ArgumentParser();ap.add_argument("phase",choices=("bars","ledgers","replay"),nargs="?",default="bars");args=ap.parse_args();{"bars":main,"ledgers":ledgers,"replay":replay}[args.phase]()
