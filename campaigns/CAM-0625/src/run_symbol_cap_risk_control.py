from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; SH=CAM/"CAM-0600"/"artifacts"/"shared"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0028"; IDS=["CAM-0600","CAM-0621","CAM-0624","CAM-0618"]; sys.path.insert(0,str(CAM/"CAM-0600"/"src"))
from deep_strategies import build_deep_variants
from repair_strategies import build_repair_variants
from run_suite import _load_or_build_fundamentals
from suite_core import load_panels

def dd(s):
 e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max()) if len(s) else 0
def stats(s):
 m=s.groupby(s.index.to_period("M")).sum(); return {"net_simple_return":float(s.sum()),"maximum_drawdown":dd(s),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"monthly_median":float(m.median()),"worst_month":float(m.min())}
def main():
 OUT.mkdir(parents=True,exist_ok=True); base=pd.read_csv(SH/"split_repaired_diagnostic_summary.csv").set_index("campaign_id"); repair=pd.read_csv(SH/"split_repaired_repair_diagnostic_summary.csv").set_index("campaign_id"); panels=load_panels(); fundamentals,_=_load_or_build_fundamentals(panels); holdings=[]; returns=[]
 for cid in IDS:
  row=(repair if cid=="CAM-0621" else base).loc[cid]; variant_id=str(row.selected_variant); builder=build_repair_variants if cid=="CAM-0621" else build_deep_variants; v=next(x for x in builder(cid,panels,fundamentals) if x.variant_id==variant_id); executed=np.zeros_like(v.weights); executed[1:]=v.weights[:-1]; ret=v.panel.open_to_next_open_return.copy(); ret[-1]=v.panel.open_to_close_return[-1]; holdings.append(pd.DataFrame(executed*.25,index=v.panel.dates,columns=v.panel.symbols.astype(str))); returns.append(pd.DataFrame(ret,index=v.panel.dates,columns=v.panel.symbols.astype(str)))
 master=pd.DatetimeIndex(sorted(set().union(*(set(h.index) for h in holdings)))); target=holdings[0].reindex(master).ffill().fillna(0)
 for h in holdings[1:]: target=target.add(h.reindex(master).ffill().fillna(0),fill_value=0)
 all_symbols=target.columns; replay=pd.read_parquet(SH/"split_repaired_quote_replay_0940.parquet"); replay=replay[replay.effective_complete].copy(); replay.session_date=pd.to_datetime(replay.session_date); quotes=replay.sort_values("campaign_id").drop_duplicates(["session_date","symbol"])[["session_date","symbol","bid_price","ask_price","reference_mid"]]; results=[]
 for cap in (None,.15,.10):
  scale=(cap/target.replace(0,np.nan)).clip(upper=1).fillna(1) if cap else pd.DataFrame(1.0,index=master,columns=all_symbols); position=target*scale; delta=position.diff().fillna(position.iloc[0]); gross=pd.Series(0.0,index=master); gross_symbol=pd.Series(0.0,index=all_symbols)
  for h,r in zip(holdings,returns):
   local_scale=scale.reindex(h.index)[h.columns]; local=(h*local_scale*r).fillna(0); local_daily=local.sum(axis=1); gross=gross.add(local_daily.reindex(master).fillna(0),fill_value=0); qlocal=local[(local.index>=pd.Timestamp("2025-05-01"))&(local.index<=pd.Timestamp("2026-04-30"))].sum(axis=0); gross_symbol=gross_symbol.add(qlocal.reindex(all_symbols).fillna(0),fill_value=0)
  bar_cost=delta.abs().sum(axis=1)*2/10000; full=gross-bar_cost; qmask=(position.index>=pd.Timestamp("2025-05-01"))&(position.index<=pd.Timestamp("2026-04-30")); qgross=gross[qmask]; qdelta=delta[qmask]; adjustments=pd.Series(0.0,index=qgross.index); symbol_adj=pd.Series(0.0,index=all_symbols)
  qmap={(r.session_date,r.symbol):r for r in quotes.itertuples(index=False)}
  for date,row in qdelta.iterrows():
   for symbol,value in row[row.abs()>1e-10].items():
    side="buy" if value>0 else "sell"; quote=qmap.get((date,symbol))
    if quote is None: raise RuntimeError(f"missing quote {date} {symbol} {side}")
    adj=abs(value)*((quote.ask_price/quote.reference_mid-1) if side=="buy" else (1-quote.bid_price/quote.reference_mid))+abs(value)*2/10000; adjustments.loc[date]+=adj; symbol_adj.loc[symbol]+=adj
  qnet=qgross-adjustments; net_symbol=(gross_symbol-symbol_adj).sort_values(ascending=False); positive=net_symbol.clip(lower=0); rec={"cap":"uncapped" if cap is None else cap,"average_gross_exposure":float(position.sum(axis=1).mean()),"maximum_gross_exposure":float(position.sum(axis=1).max()),"full":stats(full),"quote":stats(qnet),"quote_top_symbol":str(net_symbol.index[0]),"quote_top_symbol_positive_share":float(positive.iloc[0]/positive.sum()),"quote_top5_symbol_positive_share":float(positive.head(5).sum()/positive.sum()),"quote_leave_top5_symbols_out_net":float(qnet.sum()-net_symbol.head(5).sum())}; results.append(rec); full.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/f"full_{rec['cap']}.parquet",index=False); qnet.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/f"quote_{rec['cap']}.parquet",index=False); net_symbol.rename("net_pnl").rename_axis("symbol").reset_index().to_csv(OUT/f"quote_symbols_{rec['cap']}.csv",index=False)
 report={"status":"completed","run_id":"RUN-0028","variants":results,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Prespecified development risk-control test with cash residual and no leverage."}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); path=CAM/"CAM-0625"/"runs"/"RUN-0028.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="completed"; run["result"]=report; run["decision"]="Retain a cap only if concentration improves materially without destroying return/path; no further cap search."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":"RUN-0028","event":"completed","variants":results,"holdout_rows_loaded":0})+"\n")
 print(json.dumps(report,indent=2))
if __name__=="__main__": main()
