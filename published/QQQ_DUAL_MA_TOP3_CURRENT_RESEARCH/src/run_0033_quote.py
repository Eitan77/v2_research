from __future__ import annotations
import argparse,json,sys
from datetime import datetime,time
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(Path(__file__).parent));sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"))
from suite_core import evaluate_weights
from run_0033_exit_overlays import OUT,base_context,overlay,SPECS,summary
NY=ZoneInfo("America/New_York");IDS=("control","rank5","trim5","tp10","stop5")
def weights():
 p,score,mask,sig,base,sma50,ranks=base_context();return p,{n:overlay(p,sig,base,sma50,ranks,SPECS[n])[0] for n in IDS}
def ledgers():
 p,ws=weights();records={"0930":[],"0940":[]}
 for name,w in ws.items():
  exe=np.zeros_like(w);exe[1:]=w[:-1];exe=np.where(np.isfinite(p.adj_open),exe,0);prev=np.zeros(p.n_symbols)
  for i,day in enumerate(p.dates):
   delta=exe[i]-prev
   for c in np.flatnonzero(np.abs(delta)>1e-12):
    side="buy" if delta[c]>0 else "sell"
    for label,clock in (("0930",(9,30)),("0940",(9,40))):records[label].append({"variant":name,"session_date":pd.Timestamp(day).normalize(),"symbol":str(p.symbols[c]),"side":side,"delta_weight":float(abs(delta[c])),"target_ts":pd.Timestamp(datetime.combine(pd.Timestamp(day).date(),time(*clock),tzinfo=NY)).tz_convert("UTC"),"role":"entry_ask_after" if side=="buy" else "exit_bid_after"})
   prev=exe[i].copy()
 for label,rows in records.items():
  x=pd.DataFrame(rows).sort_values(["target_ts","variant","symbol"]);x.to_parquet(OUT/f"quote_ledger_{label}.parquet",index=False);x[["symbol","target_ts","role"]].drop_duplicates().to_parquet(OUT/f"quote_roles_{label}.parquet",index=False)
 print({k:len(v) for k,v in records.items()})
def cache(label):
 frames=[]
 for run in ("RUN-0030","RUN-0031","RUN-0032"):
  base=ROOT/"campaigns"/"CAM-0611"/"artifacts"/run
  for seconds in (5,30,1200):
   for prefix in ("cached_quotes_",f"quotes_"):
    path=base/(f"{prefix}{label}.parquet" if prefix.startswith("cached") else f"quotes_{label}_{seconds}s.parquet")
    if path.exists():frames.append(pd.read_parquet(path))
 for seconds in (5,30,1200):
  path=OUT/f"quote_quotes_{label}_{seconds}s.parquet"
  if path.exists():frames.append(pd.read_parquet(path))
 q=pd.concat(frames,ignore_index=True);q.target_ts=pd.to_datetime(q.target_ts,utc=True);q.quote_ts=pd.to_datetime(q.quote_ts,utc=True);return q.sort_values("quote_ts").drop_duplicates(["symbol","target_ts","role"])
def missing():
 for label in ("0930","0940"):
  r=pd.read_parquet(OUT/f"quote_roles_{label}.parquet");r.target_ts=pd.to_datetime(r.target_ts,utc=True);q=cache(label);m=r.merge(q[["symbol","target_ts","role"]],on=["symbol","target_ts","role"],how="left",indicator=True);z=m[m._merge.eq("left_only")][["symbol","target_ts","role"]];z.to_parquet(OUT/f"quote_missing_{label}.parquet",index=False);print(label,len(r),len(z))
def replay():
 p,ws=weights();keys=["symbol","target_ts","role"];merged={}
 for label in ("0930","0940"):
  l=pd.read_parquet(OUT/f"quote_ledger_{label}.parquet");l.target_ts=pd.to_datetime(l.target_ts,utc=True);q=cache(label);merged[label]=l.merge(q[keys+["quote_ts","bid_price","ask_price"]],on=keys,how="left",validate="many_to_one")
 ref=merged["0930"].copy();ref["reference_mid"]=(ref.bid_price+ref.ask_price)/2;ref=ref[["variant","session_date","symbol","side","reference_mid"]];f=merged["0940"].merge(ref,on=["variant","session_date","symbol","side"],how="left",validate="one_to_one")
 x=f.symbol.eq("XLNX")&f.session_date.eq(pd.Timestamp("2022-02-14"))&f.side.eq("sell")
 if x.any():
  base=ROOT/"campaigns"/"CAM-0600"/"artifacts"/"RUN-0042";a=pd.read_parquet(base/"xlnx_reference_quote.parquet").iloc[0];b=pd.read_parquet(base/"xlnx_terminal_quote.parquet").iloc[0];f.loc[x,"reference_mid"]=(float(a.bid_price)+float(a.ask_price))/2;f.loc[x,"bid_price"]=float(b.bid_price);f.loc[x,"ask_price"]=float(b.ask_price);f.loc[x,"session_date"]=pd.Timestamp("2022-02-11")
 ok=f.bid_price.notna()&f.ask_price.notna()&f.reference_mid.notna()&(f.bid_price>0)&(f.ask_price>=f.bid_price)&(f.reference_mid>0)
 if not ok.all():raise RuntimeError(f"missing {int((~ok).sum())}")
 f.to_parquet(OUT/"quote_fill_ledger.parquet",index=False);rows=[]
 for name,w in ws.items():
  g=f[f.variant.eq(name)];_,d,*_=evaluate_weights(p,w,0,holding="open_to_next_open",execution_lag=1)
  for extra in (0.,1.,2.,5.,10.):
   cost=np.where(g.side.eq("buy"),g.delta_weight*(g.ask_price/g.reference_mid-1),g.delta_weight*(1-g.bid_price/g.reference_mid))+g.delta_weight.to_numpy()*extra/10000;cd=pd.Series(cost,index=pd.to_datetime(g.session_date)).groupby(level=0).sum();net=d.gross_pnl.subtract(cd,fill_value=0);rows.append({"variant":name,"extra_bps":extra,**summary(net),"turnover":float(g.delta_weight.sum()),"trade_roles":len(g),"trade_sessions":int(pd.to_datetime(g.session_date).nunique()),"role_coverage":1.0});pd.DataFrame({"date":net.index,"net_pnl":net.values}).to_parquet(OUT/f"quote_daily_{name}_{extra:g}bps.parquet",index=False)
 report={"status":"completed","metrics":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin":False};(OUT/"quote_report.json").write_text(json.dumps(report,indent=2)+"\n");print(pd.DataFrame(rows).query("extra_bps==2").to_string(index=False))
if __name__=="__main__":
 ap=argparse.ArgumentParser();ap.add_argument("phase",choices=("ledgers","missing","replay"));a=ap.parse_args();globals()[a.phase]()
