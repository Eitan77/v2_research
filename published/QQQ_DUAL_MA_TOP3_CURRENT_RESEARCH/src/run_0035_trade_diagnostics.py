from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"));sys.path.insert(0,str(Path(__file__).parent))
sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0514"/"src"))
from baseline_strategies import moving_average
from suite_core import load_panels,trailing_return,trailing_vol
from fundamental_gate import FactBook
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0035";FUNDROOT=ROOT/"campaigns"/"CAM-0514"/"artifacts"/"RUN-0001"
def rolling_median(a,n):return pd.DataFrame(a).rolling(n,min_periods=n).median().to_numpy()
def main():
 OUT.mkdir(parents=True,exist_ok=True);p=load_panels()["qqq"]
 if str(p.dates.max().date())!="2026-04-30" or p.readiness.get("holdout_rows_loaded_total",0)!=0:raise RuntimeError("readiness failed")
 w=np.load(ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0034"/"weights_control.npy");exe=np.zeros_like(w);exe[1:]=w[:-1];exe=np.where(np.isfinite(p.adj_open),exe,0);ret=np.nan_to_num(p.open_to_next_open_return,nan=0);ret[-1]=np.nan_to_num(p.open_to_close_return[-1],nan=0)
 fills=pd.read_parquet(ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0034"/"quote_fill_ledger.parquet");fills=fills[fills.variant.eq("control")].copy();fills.session_date=pd.to_datetime(fills.session_date);fills["cost"]=np.where(fills.side.eq("buy"),fills.delta_weight*(fills.ask_price/fills.reference_mid-1),fills.delta_weight*(1-fills.bid_price/fills.reference_mid))+fills.delta_weight*2/10000
 score=trailing_return(p,126,21);vol=trailing_vol(p,63);sma50=moving_average(p,50);sma200=moving_average(p,200);dv=p.raw_close*p.volume;dv63=rolling_median(dv,63);v20=rolling_median(p.volume,20)
 rows=[]
 for c,sym in enumerate(p.symbols.astype(str)):
  active=exe[:,c]>1e-12;starts=np.flatnonzero(active&np.r_[True,~active[:-1]]);ends=np.flatnonzero(active&np.r_[~active[1:],True])
  for s,e in zip(starts,ends):
   decision=max(0,s-1);endcost=p.dates[min(e+1,len(p.dates)-1)];g=fills[fills.symbol.eq(sym)&fills.session_date.between(p.dates[s],endcost)];net=float((exe[s:e+1,c]*ret[s:e+1,c]).sum()-g.cost.sum());capital=float(exe[s,c]);entry=float(p.adj_open[s,c]);path=p.adj_close[s:e+1,c]/entry-1;terminal=float(p.adj_close[e,c]/entry-1) if np.isfinite(p.adj_close[e,c]) else np.nan;mfe=float(np.nanmax(path));mae=float(np.nanmin(path));rows.append({"symbol":sym,"decision_date":p.dates[decision],"entry_date":p.dates[s],"exit_date":p.dates[e],"net_pnl":net,"position_return":net/capital,"holding_sessions":e-s+1,"close_mfe":mfe,"close_mae":mae,"terminal_giveback":mfe-terminal,"winner":net>0,"large_winner":net/capital>=.20,"large_loser":net/capital<=-.10,"momentum_126_21":score[decision,c],"volatility_63":vol[decision,c],"dollar_volume_median63":dv63[decision,c],"relative_volume_1_20":p.volume[decision,c]/v20[decision,c] if np.isfinite(v20[decision,c]) and v20[decision,c]>0 else np.nan,"distance_sma50":p.adj_close[decision,c]/sma50[decision,c]-1 if np.isfinite(sma50[decision,c]) else np.nan,"distance_sma200":p.adj_close[decision,c]/sma200[decision,c]-1 if np.isfinite(sma200[decision,c]) else np.nan})
 ep=pd.DataFrame(rows);expected=3.628296357350629
 if abs(ep.net_pnl.sum()-expected)>1e-9:raise RuntimeError(f"pnl reconciliation {ep.net_pnl.sum()} {expected}")
 identity=json.loads((FUNDROOT/"sec_identity_map.json").read_text());books={}
 for sym,item in identity.items():
  cik=item.get("cik");path=FUNDROOT/"sec_cache"/"annual_facts"/f"{cik}.json" if cik else None
  books[sym]=FactBook(json.loads(path.read_text()).get("facts",[])) if path and path.exists() else FactBook([])
 snaps=[]
 for r in ep.itertuples():
  i=int(np.searchsorted(p.dates,np.datetime64(r.decision_date)));c=p.symbol_to_col[r.symbol];snap=books.get(r.symbol,FactBook([])).snapshot(pd.Timestamp(r.decision_date).date(),float(p.raw_close[i,c]),float(p.split_factor[i,c]),[]);snaps.append({k:snap.get(k) for k in ("market_cap","revenue_cagr","eps_cagr")})
 ep=pd.concat([ep.reset_index(drop=True),pd.DataFrame(snaps)],axis=1);ep.to_parquet(OUT/"trade_episodes.parquet",index=False);ep.to_csv(OUT/"trade_episodes.csv",index=False)
 features=["momentum_126_21","volatility_63","dollar_volume_median63","relative_volume_1_20","distance_sma50","distance_sma200","market_cap","revenue_cagr","eps_cagr"]
 bins=[]
 for feature in features:
  z=ep.dropna(subset=[feature]).copy()
  if z[feature].nunique()<4:continue
  z["quartile"]=pd.qcut(z[feature],4,labels=False,duplicates="drop")+1
  for q,g in z.groupby("quartile"):
   pos=g.net_pnl.clip(lower=0);bins.append({"feature":feature,"quartile":int(q),"trades":len(g),"feature_min":float(g[feature].min()),"feature_max":float(g[feature].max()),"average_position_return":float(g.position_return.mean()),"median_position_return":float(g.position_return.median()),"win_rate":float(g.winner.mean()),"net_pnl":float(g.net_pnl.sum()),"large_winners":int(g.large_winner.sum()),"large_losers":int(g.large_loser.sum()),"average_mfe":float(g.close_mfe.mean()),"average_mae":float(g.close_mae.mean()),"average_giveback":float(g.terminal_giveback.mean())})
 b=pd.DataFrame(bins);b.to_csv(OUT/"feature_quartiles.csv",index=False)
 top=ep.sort_values("net_pnl",ascending=False);positive=top.net_pnl.clip(lower=0);summary={"status":"completed","trades":len(ep),"net_pnl":float(ep.net_pnl.sum()),"average_position_return":float(ep.position_return.mean()),"median_position_return":float(ep.position_return.median()),"win_rate":float(ep.winner.mean()),"large_winners":int(ep.large_winner.sum()),"large_losers":int(ep.large_loser.sum()),"top5_positive_share":float(positive.head(5).sum()/positive.sum()),"top10_positive_share":float(positive.head(10).sum()/positive.sum()),"average_mfe":float(ep.close_mfe.mean()),"average_mae":float(ep.close_mae.mean()),"average_giveback":float(ep.terminal_giveback.mean()),"coverage":{f:float(ep[f].notna().mean()) for f in features},"fundamental_timing":"rebuilt_from_filing_date_bounded_SEC_factbooks_at_each_trade_decision","maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}
 (OUT/"diagnostic_report.json").write_text(json.dumps(summary,indent=2)+"\n");print(json.dumps(summary,indent=2));print(b.to_string(index=False))
if __name__=="__main__":main()
