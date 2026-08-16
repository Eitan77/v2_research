from __future__ import annotations
import argparse,json,sys
from datetime import datetime,time
from zoneinfo import ZoneInfo
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"))
from suite_core import load_panels
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0047";COST=9.740340418/10000
def dd(x):
 e=1+pd.Series(x).cumsum();p=np.maximum.accumulate(np.r_[1.,e.to_numpy()])[1:];return float(-(e/p-1).min())
def build():
 OUT.mkdir(parents=True,exist_ok=True);panels=load_panels();p=panels["qqq"];etf=panels["etf"]
 if pd.Timestamp(p.dates.max())!=pd.Timestamp("2026-04-30") or pd.Timestamp(etf.dates.max())>pd.Timestamp("2026-04-30"):raise RuntimeError("holdout boundary failure")
 w=np.load(ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0038"/"weights_friday.npy");control=np.zeros_like(w);control[1:]=w[:-1]
 qcol=p.symbol_to_col["QQQ"];q=pd.Series(p.adj_close[:,qcol],index=p.dates);risk=(q.rolling(10).mean()<q.rolling(20).mean()).fillna(False).to_numpy();risk_exec=np.r_[False,risk[:-1]]
 ecol=etf.symbol_to_col["SQQQ"];emap={pd.Timestamp(d):i for i,d in enumerate(etf.dates)};sopen=np.array([etf.adj_open[emap[pd.Timestamp(d)],ecol] if pd.Timestamp(d) in emap else np.nan for d in p.dates]);sclose=np.array([etf.adj_close[emap[pd.Timestamp(d)],ecol] if pd.Timestamp(d) in emap else np.nan for d in p.dates]);sr=np.ones(len(p.dates));sr[:-1]=np.divide(sopen[1:],sopen[:-1],out=np.ones(len(p.dates)-1),where=np.isfinite(sopen[1:])&np.isfinite(sopen[:-1])&(sopen[:-1]>0))-1;sr[-1]=sclose[-1]/sopen[-1]-1
 rr=np.ones_like(p.adj_open);rr[:-1]=np.divide(p.adj_open[1:],p.adj_open[:-1],out=np.ones_like(p.adj_open[:-1]),where=np.isfinite(p.adj_open[1:])&np.isfinite(p.adj_open[:-1])&(p.adj_open[:-1]>0))-1;rr[-1]=np.divide(p.adj_close[-1],p.adj_open[-1],out=np.ones(p.n_symbols),where=np.isfinite(p.adj_close[-1])&np.isfinite(p.adj_open[-1])&(p.adj_open[-1]>0))-1
 modes={"control":None,"cash_switch":0.,"half_inverse":.5,"full_inverse":1.};rows=[]
 for name,inv in modes.items():
  stock=control.copy();inverse=np.zeros(len(p.dates))
  if inv is not None:stock[risk_exec]=0;inverse[risk_exec]=inv
  stock=np.where(np.isfinite(p.adj_open),stock,0.0)
  gross=np.nansum(stock*rr,axis=1)+inverse*sr;turn=np.abs(np.diff(stock,axis=0,prepend=np.zeros((1,p.n_symbols)))).sum(1)+np.abs(np.diff(inverse,prepend=0));net=gross-turn*COST;monthly=pd.Series(net,index=p.dates).groupby(pd.DatetimeIndex(p.dates).to_period("M")).sum();recent=pd.Series(net,index=p.dates).loc[lambda x:x.index>=pd.Timestamp("2025-05-01")]
  rows.append({"variant":name,"net_return":float(net.sum()),"max_drawdown":dd(net),"recent12":float(recent.sum()),"positive_months":int((monthly>0).sum()),"negative_months":int((monthly<0).sum()),"worst_month":float(monthly.min()),"turnover":float(turn.sum()),"risk_off_sessions":int(risk_exec.sum())});pd.DataFrame({"date":p.dates,"gross_pnl":gross,"bar_net_pnl":net,"turnover":turn,"risk_off":risk_exec,"inverse_weight":inverse}).to_parquet(OUT/f"bar_daily_{name}.parquet",index=False);np.save(OUT/f"stock_weights_{name}.npy",stock);np.save(OUT/f"inverse_weights_{name}.npy",inverse)
 report={"status":"completed_bar_stage","flip":"QQQ_SMA10_below_SMA20_completed_close_next_session","cost_bps_per_turnover":COST*10000,"metrics":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0};(OUT/"bar_report.json").write_text(json.dumps(report,indent=2)+"\n");print(pd.DataFrame(rows).to_string(index=False))
def ledgers():
 p=load_panels()["qqq"];ny=ZoneInfo("America/New_York");rows=[]
 for name in ("cash_switch","half_inverse","full_inverse"):
  stock=np.load(OUT/f"stock_weights_{name}.npy");inv=np.load(OUT/f"inverse_weights_{name}.npy");prev=np.zeros(p.n_symbols);previnv=0.
  for i,d in enumerate(p.dates):
   delta=stock[i]-prev
   changes=[(("AMD" if str(p.symbols[c])=="XLNX" and pd.Timestamp(d)>=pd.Timestamp("2022-02-14") else str(p.symbols[c])),float(delta[c])) for c in np.flatnonzero(np.abs(delta)>1e-12)]
   if abs(inv[i]-previnv)>1e-12:changes.append(("SQQQ",float(inv[i]-previnv)))
   for symbol,dw in changes:
    side="buy" if dw>0 else "sell"
    for label,clock in (("0930",(9,30)),("0940",(9,40))):rows.append({"variant":name,"label":label,"session_date":pd.Timestamp(d),"symbol":symbol,"side":side,"delta_weight":abs(dw),"target_ts":pd.Timestamp(datetime.combine(pd.Timestamp(d).date(),time(*clock),tzinfo=ny)).tz_convert("UTC"),"role":"entry_ask_after" if side=="buy" else "exit_bid_after"})
   prev=stock[i].copy();previnv=inv[i]
 l=pd.DataFrame(rows);l.to_parquet(OUT/"quote_ledger.parquet",index=False)
 for label,g in l.groupby("label"):g[["symbol","target_ts","role"]].drop_duplicates().to_parquet(OUT/f"roles_{label}.parquet",index=False)
 print(json.dumps({"ledger_rows":len(l),"unique_0930_roles":len(pd.read_parquet(OUT/'roles_0930.parquet')),"unique_0940_roles":len(pd.read_parquet(OUT/'roles_0940.parquet'))}))
def replay():
 l=pd.read_parquet(OUT/"quote_ledger.parquet");l.target_ts=pd.to_datetime(l.target_ts,utc=True);merged={}
 for label in ("0930","0940"):
  q=pd.concat([pd.read_parquet(OUT/f"quotes_{label}_5s.parquet"),pd.read_parquet(OUT/f"quotes_{label}_extra.parquet"),pd.read_parquet(OUT/f"quotes_{label}_successor.parquet")],ignore_index=True);q.target_ts=pd.to_datetime(q.target_ts,utc=True);q=q.drop_duplicates(["symbol","target_ts","role"],keep="first");merged[label]=l[l.label.eq(label)].merge(q[["symbol","target_ts","role","quote_ts","bid_price","ask_price"]],on=["symbol","target_ts","role"],how="left",validate="many_to_one")
 ref=merged["0930"].copy();ref["reference_mid"]=(ref.bid_price+ref.ask_price)/2;ref=ref[["variant","session_date","symbol","side","reference_mid"]];f=merged["0940"].merge(ref,on=["variant","session_date","symbol","side"],how="left",validate="one_to_one")
 if f[["reference_mid","bid_price","ask_price"]].isna().any().any():raise RuntimeError("incomplete exact quotes")
 f.to_parquet(OUT/"quote_fill_ledger.parquet",index=False);rows=[]
 for name in ("cash_switch","half_inverse","full_inverse"):
  base=pd.read_parquet(OUT/f"bar_daily_{name}.parquet");base.date=pd.to_datetime(base.date);ff=f[f.variant.eq(name)]
  execdrag=np.where(ff.side.eq("buy"),ff.delta_weight*(ff.ask_price/ff.reference_mid-1),ff.delta_weight*(1-ff.bid_price/ff.reference_mid))
  for bps in (0.,1.,2.,5.,10.):
   cost=pd.Series(execdrag+ff.delta_weight.to_numpy()*bps/10000,index=pd.to_datetime(ff.session_date)).groupby(level=0).sum();net=base.set_index("date").gross_pnl.subtract(cost,fill_value=0);monthly=net.groupby(net.index.to_period("M")).sum();recent=net[net.index>=pd.Timestamp("2025-05-01")];rows.append({"variant":name,"extra_bps":bps,"net_return":float(net.sum()),"max_drawdown":dd(net),"recent12":float(recent.sum()),"positive_months":int((monthly>0).sum()),"negative_months":int((monthly<0).sum()),"worst_month":float(monthly.min())});pd.DataFrame({"date":net.index,"net_pnl":net.values}).to_parquet(OUT/f"quote_daily_{name}_{bps:g}bps.parquet",index=False)
 report={"status":"completed","metrics":rows,"quote_role_coverage":1.0,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0};(OUT/"quote_report.json").write_text(json.dumps(report,indent=2)+"\n");print(pd.DataFrame(rows)[lambda x:x.extra_bps.eq(2)].to_string(index=False))
if __name__=="__main__":
 ap=argparse.ArgumentParser();ap.add_argument("phase",choices=("build","ledgers","replay"),nargs="?",default="build");a=ap.parse_args();globals()[a.phase]()
