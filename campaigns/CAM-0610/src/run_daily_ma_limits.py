from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import duckdb, numpy as np, pandas as pd, yaml

ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src"))
from suite_core import CAMPAIGNS
PARENT=CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0025"; OUT=CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0030"; RUN=CAMPAIGNS/"CAM-0610"/"runs"/"RUN-0030.yaml"

def prepare():
 OUT.mkdir(parents=True,exist_ok=True); led=pd.read_parquet(PARENT/"ledger_0940.parquet"); led.target_ts=pd.to_datetime(led.target_ts,utc=True); keys=led[["session_date","symbol"]].drop_duplicates(); frames=[]
 with duckdb.connect(r"D:\AlgoResearch\data\catalog.duckdb",read_only=True) as con:
  con.execute("SET threads=16"); con.execute("SET memory_limit='20GB'"); keyed=keys.copy(); keyed["month"]=pd.to_datetime(keyed.session_date).dt.to_period("M")
  for month,mk in keyed.groupby("month"):
   syms=",".join("'"+s.replace("'","''")+"'" for s in sorted(mk.symbol.unique())); start=month.start_time.date(); end=min(month.end_time.date(),pd.Timestamp("2026-04-30").date())
   x=con.execute(f"""SELECT symbol,timestamp,high,low,date FROM read_parquet('D:/AlgoResearch/data/raw/alpaca/market/stocks/bars_1m/**/*.parquet',union_by_name=true,hive_partitioning=true) WHERE date BETWEEN DATE '{start}' AND DATE '{end}' AND symbol IN ({syms}) AND feed='sip' AND adjustment='raw' QUALIFY row_number() OVER(PARTITION BY symbol,timestamp,timeframe,feed,adjustment ORDER BY COALESCE(TRY_CAST(ingested_at AS TIMESTAMP),TIMESTAMP '1900-01-01') DESC,COALESCE(source_ingestion_id,'') DESC)=1""").df(); allowed=set(zip(mk.symbol.astype(str),pd.to_datetime(mk.session_date).dt.date)); frames.append(x[[((str(s),pd.Timestamp(d).date()) in allowed) for s,d in zip(x.symbol,x.date)]])
 bars=pd.concat(frames); bars.timestamp=pd.to_datetime(bars.timestamp,utc=True,format="mixed"); bars.to_parquet(OUT/"minute_bars.parquet",index=False)
 roles=[]
 for r in led.itertuples(index=False):
  for minutes in (5,15): roles.append({"symbol":r.symbol,"target_ts":r.target_ts+pd.Timedelta(minutes=minutes),"role":r.role})
 pd.DataFrame(roles).drop_duplicates().to_parquet(OUT/"deadline_roles.parquet",index=False); print(json.dumps({"orders":len(led),"minute_rows":len(bars),"deadline_roles":len(pd.DataFrame(roles).drop_duplicates()),"holdout_rows_loaded":0},indent=2))

def loadq(prefix,directory):
 fs=[]
 for sec in (5,30,120):
  p=directory/f"{prefix}_{sec}s.parquet"
  if p.exists(): x=pd.read_parquet(p); x["priority"]=sec; fs.append(x)
 q=pd.concat(fs).sort_values("priority").drop_duplicates(["symbol","target_ts","role"]); q.target_ts=pd.to_datetime(q.target_ts,utc=True); return q

def dd(s): e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max())

def replay():
 led=pd.read_parquet(PARENT/"ledger_0940.parquet"); led.target_ts=pd.to_datetime(led.target_ts,utc=True); startq=loadq("quotes_0940",PARENT); led=led.merge(startq[["symbol","target_ts","role","bid_price","ask_price"]],on=["symbol","target_ts","role"],validate="one_to_one")
 q0=loadq("quotes_0930",PARENT); l0=pd.read_parquet(PARENT/"ledger_0930.parquet"); l0.target_ts=pd.to_datetime(l0.target_ts,utc=True); l0=l0.merge(q0[["symbol","target_ts","role","bid_price","ask_price"]],on=["symbol","target_ts","role"],validate="one_to_one"); l0["reference_mid"]=(l0.bid_price+l0.ask_price)/2; led=led.merge(l0[["session_date","symbol","side","reference_mid"]],on=["session_date","symbol","side"],validate="one_to_one")
 deadline=loadq("deadline_quotes",OUT); bars=pd.read_parquet(OUT/"minute_bars.parquet"); bars.timestamp=pd.to_datetime(bars.timestamp,utc=True); groups={(s,pd.Timestamp(d)):g.sort_values("timestamp") for (s,d),g in bars.groupby(["symbol",pd.to_datetime(bars.date)])}; details=[]
 for r in led.itertuples(index=False):
  path=groups[(r.symbol,pd.Timestamp(r.session_date))]
  for wait in (5,15):
   ts=r.target_ts+pd.Timedelta(minutes=wait); fq=deadline[(deadline.symbol==r.symbol)&(deadline.target_ts==ts)&(deadline.role==r.role)].iloc[0]
   eligible=path[(path.timestamp>=r.target_ts+pd.Timedelta(minutes=1))&(path.timestamp<=ts)]
   for pen in (0,1):
    if r.side=="buy": level=r.bid_price; passive=bool((eligible.low<=level*(1-pen/10000)).any()); px=level if passive else fq.ask_price
    else: level=r.ask_price; passive=bool((eligible.high>=level*(1+pen/10000)).any()); px=level if passive else fq.bid_price
    details.append({"session_date":r.session_date,"symbol":r.symbol,"side":r.side,"delta_weight":r.delta_weight,"wait_minutes":wait,"penetration_bps":pen,"passive":passive,"execution_price":px,"reference_mid":r.reference_mid})
 d=pd.DataFrame(details); d.to_parquet(OUT/"limit_orders.parquet",index=False); bar=pd.read_parquet(CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0024"/"variants"/"sp500__ma200__daily__top10__momentum__cost_2bps"/"daily.parquet"); bar.date=pd.to_datetime(bar.date); bar=bar[(bar.date>=pd.Timestamp("2025-05-01"))&(bar.date<=pd.Timestamp("2026-04-30"))].set_index("date"); rows=[]
 for (wait,pen),g in d.groupby(["wait_minutes","penetration_bps"]):
  for extra in (0,1,2):
   adj=np.where(g.side.eq("buy"),g.delta_weight*(g.execution_price/g.reference_mid-1),g.delta_weight*(1-g.execution_price/g.reference_mid))+g.delta_weight.to_numpy()*extra/10000; daily=bar.gross_pnl.subtract(pd.Series(np.asarray(adj),index=pd.to_datetime(g.session_date)).groupby(level=0).sum(),fill_value=0); m=daily.groupby(daily.index.to_period("M")).sum(); rows.append({"wait_minutes":wait,"penetration_bps":pen,"extra_adverse_bps_per_side":extra,"net_simple_return":float(daily.sum()),"maximum_drawdown":dd(daily),"passive_fill_rate":float(g.passive.mean()),"trade_session_fraction":float(pd.to_datetime(g.session_date).nunique()/len(bar)),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"monthly_average":float(m.mean()),"monthly_median":float(m.median()),"worst_month":float(m.min()),"best_month":float(m.max())})
 m=pd.DataFrame(rows); m.to_csv(OUT/"limit_metrics.csv",index=False); report={"status":"completed","run_id":"RUN-0030","metrics":m.to_dict("records"),"queue_warning":"through-price does not prove queue position; unfilled remainders cross at deadline","maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n"); r=yaml.safe_load(RUN.read_text()); r["status"]="completed"; r["result"]=report; r["decision"]="Prefer only if limit-then-cross improves marketable control under 1 bp penetration; still require paper queue evidence."; RUN.write_text(yaml.safe_dump(r,sort_keys=False)); print(m.to_string(index=False))

if __name__=="__main__":
 p=argparse.ArgumentParser(); p.add_argument("phase",choices=["prepare","replay"]); a=p.parse_args(); prepare() if a.phase=="prepare" else replay()
