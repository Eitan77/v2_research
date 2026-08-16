from __future__ import annotations
import argparse,json,sys
from datetime import datetime,time
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0611"/"src"));sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"))
import run_0026_aligned_sp500_dual as aligned
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0043";NY=ZoneInfo("America/New_York");KEY=["symbol","target_ts","role"]
def build():
 p,base_weights,_=aligned.build()
 if pd.Timestamp(p.dates.max())!=pd.Timestamp("2026-04-30"):raise RuntimeError("discovery cutoff mismatch")
 daily=pd.read_parquet(OUT/"sp500_daily_all_adjusted_apr_jun.parquet");daily.date=pd.to_datetime(daily.date);ext_dates=pd.DatetimeIndex(sorted(daily.loc[daily.date.between("2026-05-01","2026-06-30"),"date"].unique()));eval_dates=ext_dates[ext_dates>=pd.Timestamp("2026-06-01")]
 symbols=p.symbols.astype(str);cols=p.symbol_to_col;n=len(ext_dates);m=len(symbols);op=np.full((n,m),np.nan);cl=np.full_like(op,np.nan);dv=np.full_like(op,np.nan)
 mem=pd.read_parquet(ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0026"/"aligned_membership"/"sp500_pit_membership_daily.parquet");mem.date=pd.to_datetime(mem.date);last_members=set(mem[(mem.date.eq(pd.Timestamp("2026-04-30")))&mem.is_member].symbol.astype(str));member=np.array([s in last_members for s in symbols])
 returned=set(daily.symbol.astype(str));missing_members=sorted(last_members-returned)
 for s in symbols:
  if s not in returned:continue
  c=cols[s];a=daily[(daily.symbol.eq(s))&daily.date.eq(pd.Timestamp("2026-04-30"))]
  if a.empty or not np.isfinite(p.adj_close[-1,c]):continue
  scale=float(p.adj_close[-1,c]/a.close.iloc[0]);g=daily[daily.symbol.eq(s)].set_index("date")
  for i,d in enumerate(ext_dates):
   if d in g.index:op[i,c]=float(g.loc[d,"open"])*scale;cl[i,c]=float(g.loc[d,"close"])*scale;dv[i,c]=float(g.loc[d,"close"])*float(g.loc[d,"volume"])
 comb_close=pd.concat([pd.DataFrame(p.adj_close,index=p.dates),pd.DataFrame(cl,index=ext_dates)]);comb_dv=pd.concat([pd.DataFrame(p.raw_close*p.volume,index=p.dates),pd.DataFrame(dv,index=ext_dates)]);s50=comb_close.rolling(50,min_periods=50).mean();s200=comb_close.rolling(200,min_periods=200).mean();dv63=comb_dv.rolling(63,min_periods=32).median();tri=pd.DataFrame(p.total_return_index,index=p.dates);last=tri.iloc[-1].to_numpy();prev=comb_close.loc[p.dates[-1]].to_numpy()
 for d in ext_dates:
  cur=comb_close.loc[d].to_numpy();step=np.divide(cur,prev,out=np.ones_like(cur),where=np.isfinite(cur)&np.isfinite(prev)&(prev>0));last=last*step;tri.loc[d]=last;prev=cur
 periods=ext_dates.to_period("W-FRI");signal_idx=[i for i in range(n-1) if periods[i+1]!=periods[i]]
 targets=[];target_by_signal={}
 for i in signal_idx:
  d=ext_dates[i];loc=tri.index.get_loc(d);score=tri.iloc[loc-21].to_numpy()/tri.iloc[loc-147].to_numpy()-1;ready=member&np.isfinite(cl[i])&(s50.loc[d].to_numpy()>s200.loc[d].to_numpy())&np.isfinite(score)&np.isfinite(dv63.loc[d].to_numpy());eligible=np.flatnonzero(ready);k=max(1,int(np.ceil(len(eligible)*.5))) if len(eligible) else 0;liq=eligible[np.argsort(dv63.loc[d].to_numpy()[eligible])[-k:]] if k else np.array([],int);chosen=liq[np.argsort(score[liq])[-min(3,len(liq)):]] if len(liq) else np.array([],int);t=np.zeros(m);t[chosen]=1/len(chosen) if len(chosen) else 0;target_by_signal[i]=t;targets.append({"signal_date":str(d.date()),"selected":[str(symbols[c]) for c in chosen]})
 discovery_executed=np.zeros_like(base_weights);discovery_executed[1:]=base_weights[:-1];current=discovery_executed[-1].copy();pending={ext_dates[i+1]:t for i,t in target_by_signal.items() if i+1<n};executed_ext=np.zeros((n,m))
 for i,d in enumerate(ext_dates):
  if d in pending:current=pending[d]
  executed_ext[i]=current
 eval_idx=np.array([ext_dates.get_loc(d) for d in eval_dates]);executed=executed_ext[eval_idx];gross=np.zeros(len(eval_dates))
 for i,j in enumerate(eval_idx):
  r=(np.divide(op[j+1],op[j],out=np.ones(m),where=np.isfinite(op[j+1])&np.isfinite(op[j])&(op[j]>0))-1) if i<len(eval_dates)-1 else (np.divide(cl[j],op[j],out=np.ones(m),where=np.isfinite(cl[j])&np.isfinite(op[j])&(op[j]>0))-1);gross[i]=np.nansum(executed[i]*r)
 rows=[];prevw=executed_ext[eval_idx[0]-1].copy()
 for i,d in enumerate(eval_dates):
  if d not in pending:continue
  delta=executed[i]-prevw
  for c in np.flatnonzero(np.abs(delta)>1e-12):
   side="buy" if delta[c]>0 else "sell"
   for label,clock in (("0930",(9,30)),("0940",(9,40))):rows.append({"label":label,"session_date":d,"symbol":str(symbols[c]),"side":side,"delta_weight":float(abs(delta[c])),"target_ts":pd.Timestamp(datetime.combine(d.date(),time(*clock),tzinfo=NY)).tz_convert("UTC"),"role":"entry_ask_after" if side=="buy" else "exit_bid_after"})
  prevw=executed[i].copy()
 ledger=pd.DataFrame(rows);ledger.to_parquet(OUT/"quote_ledger.parquet",index=False)
 for label,g in ledger.groupby("label"):g[KEY].drop_duplicates().to_parquet(OUT/f"roles_{label}.parquet",index=False)
 pd.DataFrame({"date":eval_dates,"gross_pnl":gross}).to_parquet(OUT/"gross_daily.parquet",index=False);np.save(OUT/"executed_weights.npy",executed);(OUT/"targets.json").write_text(json.dumps(targets,indent=2)+"\n");report={"status":"passed","evaluation_start":str(eval_dates.min().date()),"evaluation_end":str(eval_dates.max().date()),"membership_maximum_date":"2026-04-30","sessions_using_last_known_membership":len(eval_dates),"members_at_cutoff":len(last_members),"member_symbols_missing_provider_bars":missing_members,"targets":targets,"quote_roles":len(ledger),"rows_after_june":0};(OUT/"build_report.json").write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report,indent=2))
def replay():
 l=pd.read_parquet(OUT/"quote_ledger.parquet");q30=pd.read_parquet(OUT/"quotes_0930_5s.parquet");q40=pd.read_parquet(OUT/"quotes_0940_5s.parquet")
 for q in (q30,q40):q["target_ts"]=pd.to_datetime(q.target_ts,utc=True);q["session_date"]=q.target_ts.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
 refs=q30.assign(reference_mid=(q30.bid_price+q30.ask_price)/2)[["symbol","session_date","reference_mid"]];fills=q40[["symbol","session_date","bid_price","ask_price","quote_ts"]];t=l[l.label.eq("0940")].merge(refs,on=["symbol","session_date"],how="left",validate="one_to_one").merge(fills,on=["symbol","session_date"],how="left",validate="one_to_one");t["fill_price"]=np.where(t.side.eq("buy"),t.ask_price,t.bid_price);t["execution_drag"]=np.where(t.side.eq("buy"),t.fill_price/t.reference_mid-1,1-t.fill_price/t.reference_mid);t["cost"]=t.delta_weight*t.execution_drag+t.delta_weight*.0002
 if t[["reference_mid","fill_price","cost"]].isna().any().any():raise RuntimeError("incomplete quotes")
 d=pd.read_parquet(OUT/"gross_daily.parquet");d.date=pd.to_datetime(d.date);d["cost"]=d.date.map(t.groupby("session_date").cost.sum()).fillna(0);d["net_pnl"]=d.gross_pnl-d.cost;eq=1+d.net_pnl.cumsum();peak=np.maximum.accumulate(np.r_[1.,eq.to_numpy()])[1:];report={"status":"completed","june_net_fixed_base_pct":float(100*d.net_pnl.sum()),"june_gross_pct":float(100*d.gross_pnl.sum()),"maximum_drawdown_pct":float(-100*(eq/peak-1).min()),"positive_days":int((d.net_pnl>0).sum()),"negative_days":int((d.net_pnl<0).sum()),"best_day_pct":float(100*d.net_pnl.max()),"worst_day_pct":float(100*d.net_pnl.min()),"quote_coverage":1.0,"turnover":float(t.delta_weight.sum()),"maximum_loaded_date":"2026-06-30","rows_after_june":0};t.to_parquet(OUT/"quote_fill_ledger.parquet",index=False);d.to_parquet(OUT/"oos_daily_net.parquet",index=False);(OUT/"oos_report.json").write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report,indent=2))
if __name__=="__main__":ap=argparse.ArgumentParser();ap.add_argument("phase",choices=("build","replay"));a=ap.parse_args();globals()[a.phase]()
