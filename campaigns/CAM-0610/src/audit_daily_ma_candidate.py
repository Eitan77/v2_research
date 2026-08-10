from __future__ import annotations

import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"))
from baseline_strategies import moving_average
from deep_strategies import active_trend_rank
from suite_core import CAMPAIGNS, load_panels

OUT=CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0026"; RUN=CAMPAIGNS/"CAM-0610"/"runs"/"RUN-0026.yaml"

def dd(s):
 e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max())

def stats(s):
 s=s.sort_index(); m=s.groupby(s.index.to_period("M")).sum(); pos=s.clip(lower=0).sort_values(ascending=False)
 return {"net_simple_return":float(s.sum()),"maximum_drawdown":dd(s),"green_days":int((s>0).sum()),"red_days":int((s<0).sum()),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"monthly_average":float(m.mean()),"monthly_median":float(m.median()),"worst_month":float(m.min()),"best_month":float(m.max()),"top5_day_positive_share":float(pos.head(5).sum()/pos.sum())}

def main():
 OUT.mkdir(parents=True,exist_ok=True); p=load_panels()["sp500"]
 w=active_trend_rank(p,p.adj_close>moving_average(p,200),np.arange(p.n_dates),10,"momentum")
 ex=np.zeros_like(w); ex[1:]=w[:-1]
 gross=ex*np.nan_to_num(p.open_to_next_open_return,nan=0.0); gross[-1]=ex[-1]*np.nan_to_num(p.open_to_close_return[-1],nan=0.0)
 dates=pd.to_datetime(p.dates); mask=(dates>=pd.Timestamp("2025-05-01"))&(dates<=pd.Timestamp("2026-04-30"))
 symbol=pd.Series(gross[mask].sum(axis=0),index=p.symbols.astype(str))
 replay=pd.read_parquet(CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0025"/"ledger_0940.parquet")
 qs=[]
 for sec in (5,30,120):
  q=pd.read_parquet(CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0025"/f"quotes_0940_{sec}s.parquet"); q["priority"]=sec; qs.append(q)
 q=pd.concat(qs).sort_values("priority").drop_duplicates(["symbol","target_ts","role"]); replay.target_ts=pd.to_datetime(replay.target_ts,utc=True); q.target_ts=pd.to_datetime(q.target_ts,utc=True)
 z=replay.merge(q[["symbol","target_ts","role","bid_price","ask_price"]],on=["symbol","target_ts","role"],validate="one_to_one")
 ref=pd.read_parquet(CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0025"/"ledger_0930.parquet"); q0=pd.read_parquet(CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0025"/"quotes_0930_5s.parquet"); ref.target_ts=pd.to_datetime(ref.target_ts,utc=True); q0.target_ts=pd.to_datetime(q0.target_ts,utc=True)
 ref=ref.merge(q0[["symbol","target_ts","role","bid_price","ask_price"]],on=["symbol","target_ts","role"],validate="one_to_one"); ref["reference_mid"]=(ref.bid_price+ref.ask_price)/2
 z=z.merge(ref[["session_date","symbol","side","reference_mid"]],on=["session_date","symbol","side"],validate="one_to_one")
 z["adj"]=np.where(z.side.eq("buy"),z.delta_weight*(z.ask_price/z.reference_mid-1),z.delta_weight*(1-z.bid_price/z.reference_mid))+z.delta_weight*2/10000
 symbol=symbol.subtract(z.groupby("symbol").adj.sum(),fill_value=0).sort_values(ascending=False)
 daily=pd.read_parquet(CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0025"/"daily_0940_2bps.parquet"); daily.date=pd.to_datetime(daily.date); daily=daily.set_index("date").net_pnl
 positive=symbol.clip(lower=0); total=float(positive.sum())
 symbol.rename("net_pnl").rename_axis("symbol").reset_index().to_csv(OUT/"quote_symbol_contributions.csv",index=False)
 monthly=daily.groupby(daily.index.to_period("M")).sum(); monthly.rename("net_pnl").rename_axis("month").reset_index().to_csv(OUT/"quote_monthly.csv",index=False)
 full=pd.read_parquet(CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0024"/"variants"/"sp500__ma200__daily__top10__momentum__cost_2bps"/"daily.parquet"); full.date=pd.to_datetime(full.date); full=full.set_index("date").net_pnl
 folds={"2021":stats(full.loc["2021"]),"2022_2023":stats(full.loc["2022":"2023"]),"2024_to_cutoff":stats(full.loc["2024":"2026-04-30"])}
 report={"status":"completed","run_id":"RUN-0026","quote":stats(daily),"quote_symbol_concentration":{"symbols":int((symbol!=0).sum()),"top_symbol":str(symbol.index[0]),"top_symbol_net":float(symbol.iloc[0]),"top_symbol_positive_share":float(positive.iloc[0]/total),"top5_symbol_positive_share":float(positive.head(5).sum()/total),"leave_top_symbol_out_net":float(daily.sum()-symbol.iloc[0]),"leave_top5_symbols_out_net":float(daily.sum()-symbol.head(5).sum()),"sndk_net":float(symbol.get("SNDK",0.0))},"folds":folds,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}
 (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n")
 r=yaml.safe_load(RUN.read_text()); r["status"]="completed"; r["result"]=report; r["decision"]="Reject if the quote-year result depends materially on SNDK, the top five symbols, or one fold."; RUN.write_text(yaml.safe_dump(r,sort_keys=False))
 print(json.dumps(report,indent=2)); print(symbol.head(15).to_string()); print(monthly.to_string())

if __name__=="__main__": main()
