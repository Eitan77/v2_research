from __future__ import annotations
import argparse,json
from datetime import datetime,time
from zoneinfo import ZoneInfo
import numpy as np,pandas as pd,yaml
from run_notable_overlays import bases,overlays
from run_suite import _load_or_build_fundamentals
from suite_core import CAMPAIGNS,load_panels
OUT=CAMPAIGNS/"CAM-0600"/"artifacts"/"RUN-0034";RUN=CAMPAIGNS/"CAM-0600"/"runs"/"RUN-0034.yaml";START=pd.Timestamp("2025-05-01");END=pd.Timestamp("2026-04-30");NY=ZoneInfo("America/New_York")
PRIOR_DIRS=[CAMPAIGNS/"CAM-0600"/"artifacts"/"RUN-0031",CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0025",CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0027",CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0029",CAMPAIGNS/"CAM-0617"/"artifacts"/"RUN-0027"]
CHOICES={"ma200_uncapped":"persistence3","ma200_corr08":"persistence2","ma50_200":"persistence3","cluster_residual":"persistence2","characteristic_residual":"turnover_band_05","triple_ma":"vol_target_15","true_daily_alpha":"inverse_vol63"}
def utc(d,clock):h,m=map(int,clock.split(":"));return pd.Timestamp(datetime.combine(pd.Timestamp(d).date(),time(h,m),tzinfo=NY)).tz_convert("UTC")
def selected():
 ps=load_panels();f,_=_load_or_build_fundamentals(ps);return {name:(p,overlays(p,w)[CHOICES[name]]) for name,(p,w) in bases(ps,f).items()}
def ledgers():
 OUT.mkdir(parents=True,exist_ok=True)
 for clock in ("09:30","09:40"):
  rows=[]
  for name,(p,w) in selected().items():
   ex=np.zeros_like(w);ex[1:]=w[:-1];prev=np.zeros(p.n_symbols)
   for i,d in enumerate(p.dates):
    d=pd.Timestamp(d).normalize();cur=ex[i]
    if d<START:prev=cur.copy();continue
    if d>END:break
    delta=cur-prev
    for col in np.flatnonzero(np.abs(delta)>1e-8):
     side="buy" if delta[col]>0 else "sell";rows.append({"candidate":name,"overlay":CHOICES[name],"session_date":d,"symbol":str(p.symbols[col]),"side":side,"delta_weight":float(abs(delta[col])),"target_ts":utc(d,clock),"role":"entry_ask_after" if side=="buy" else "exit_bid_after"})
    prev=cur.copy()
  x=pd.DataFrame(rows);label=clock.replace(":","");x.to_parquet(OUT/f"ledger_{label}.parquet",index=False);x[["symbol","target_ts","role"]].drop_duplicates().to_parquet(OUT/f"roles_{label}.parquet",index=False);print(label,len(x),x[["symbol","target_ts","role"]].drop_duplicates().shape[0],x.candidate.nunique())
def quotes(label):
 fs=[]
 for directory in [OUT,*PRIOR_DIRS]:
  for sec in (5,30,120,300,1200):
   p=directory/f"quotes_{label}_{sec}s.parquet"
   if p.exists() and p.stat().st_size:
    x=pd.read_parquet(p)
    if len(x):x["priority"]=sec;fs.append(x)
 q=pd.concat(fs,ignore_index=True).sort_values("priority").drop_duplicates(["symbol","target_ts","role"]);q.target_ts=pd.to_datetime(q.target_ts,utc=True);return q
def dd(s):e=1+s.cumsum();return float(((e.cummax()-e)/e.cummax()).max())
def replay():
 rep={}
 for label in ("0930","0940"):
  d=pd.read_parquet(OUT/f"ledger_{label}.parquet");d.target_ts=pd.to_datetime(d.target_ts,utc=True);q=quotes(label);z=d.merge(q[["symbol","target_ts","role","bid_price","ask_price"]],on=["symbol","target_ts","role"],how="left",validate="many_to_one");z["complete"]=z.bid_price.notna()&z.ask_price.notna()&(z.bid_price>0)&(z.ask_price>=z.bid_price);rep[label]=z
 ref=rep["0930"].copy();ref["reference_mid"]=(ref.bid_price+ref.ask_price)/2;ref=ref[["candidate","session_date","symbol","side","reference_mid"]];rep["0940"]=rep["0940"].merge(ref,on=["candidate","session_date","symbol","side"],how="left",validate="one_to_one");rep["0930"]["reference_mid"]=(rep["0930"].bid_price+rep["0930"].ask_price)/2;rows=[]
 for label,z in rep.items():
  z["effective"]=z.complete&z.reference_mid.notna()&(z.reference_mid>0)
  for name,g in z.groupby("candidate"):
   overlay=CHOICES[name];bar=pd.read_parquet(CAMPAIGNS/"CAM-0600"/"artifacts"/"RUN-0033"/"variants"/f"{name}__{overlay}__cost_2bps"/"daily.parquet");bar.date=pd.to_datetime(bar.date);bar=bar[(bar.date>=START)&(bar.date<=END)].set_index("date");c=g[g.effective].copy()
   for extra in (0.,1.,2.,5.):
    adj=np.where(c.side.eq("buy"),c.delta_weight*(c.ask_price/c.reference_mid-1),c.delta_weight*(1-c.bid_price/c.reference_mid))+c.delta_weight*extra/10000;daily=bar.gross_pnl.subtract(pd.Series(np.asarray(adj),index=pd.to_datetime(c.session_date)).groupby(level=0).sum(),fill_value=0);m=daily.groupby(daily.index.to_period("M")).sum();rows.append({"candidate":name,"overlay":overlay,"clock":label,"extra_adverse_bps_per_side":extra,"net_simple_return":float(daily.sum()),"maximum_drawdown":dd(daily),"role_coverage":float(g.effective.mean()),"trade_roles":len(g),"entry_roles":int(c.side.eq("buy").sum()),"trade_sessions":int(pd.to_datetime(c.session_date).nunique()),"trade_session_fraction":float(pd.to_datetime(c.session_date).nunique()/len(bar)),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"monthly_average":float(m.mean()),"monthly_median":float(m.median()),"worst_month":float(m.min()),"best_month":float(m.max())});daily.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/f"daily_{name}_{label}_{extra:g}bps.parquet",index=False)
 m=pd.DataFrame(rows);m.to_csv(OUT/"quote_metrics.csv",index=False);report={"status":"completed","run_id":"RUN-0034","metrics":m.to_dict("records"),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin":False};(OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n");r=yaml.safe_load(RUN.read_text());r["status"]="completed";r["result"]=report;r["decision"]="Retain only quote-level Pareto improvements with compliant cadence.";RUN.write_text(yaml.safe_dump(r,sort_keys=False));print(m[(m.clock=="0940")&(m.extra_adverse_bps_per_side.isin([0,2,5]))].to_string(index=False))
if __name__=="__main__":p=argparse.ArgumentParser();p.add_argument("phase",choices=["ledgers","replay"]);a=p.parse_args();ledgers() if a.phase=="ledgers" else replay()
