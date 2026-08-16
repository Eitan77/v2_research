from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];A=ROOT/"campaigns"/"CAM-0611"/"artifacts";OUT=A/"RUN-0042"
def main():
 dev=pd.read_parquet(A/"RUN-0038"/"quote_daily_friday_2bps.parquet");oos=pd.concat([pd.read_parquet(A/r/"oos_daily_net.parquet") for r in ("RUN-0040","RUN-0041","RUN-0042")],ignore_index=True);strategy=pd.concat([dev[dev.date.between("2025-08-11","2026-04-30")],oos],ignore_index=True);strategy.date=pd.to_datetime(strategy.date);strategy=strategy.sort_values("date");strategy["value"]=100*strategy.net_pnl.cumsum()
 sdev=pd.read_parquet(A/"RUN-0026"/"daily_quote_2bps.parquet");soos=pd.read_parquet(A/"RUN-0045"/"oos_daily_net.parquet");sp500=pd.concat([sdev[pd.to_datetime(sdev.date).between("2025-08-11","2026-04-30")],soos],ignore_index=True);sp500.date=pd.to_datetime(sp500.date);sp500=sp500.sort_values("date");sp500["value"]=100*sp500.net_pnl.cumsum()
 bench=pd.read_parquet(OUT/"chart_benchmarks_last_year.parquet");bench.date=pd.to_datetime(bench.date);series={"QQQ strategy":[{"date":str(d.date()),"value":round(float(v),4)} for d,v in zip(strategy.date,strategy.value)],"S&P strategy":[{"date":str(d.date()),"value":round(float(v),4)} for d,v in zip(sp500.date,sp500.value)]};monthly={}
 for symbol,g in bench.groupby("symbol"):
  g=g.sort_values("date");base=float(g.open.iloc[0]);series[symbol]=[{"date":str(d.date()),"value":round(float(100*(c/base-1)),4)} for d,c in zip(g.date,g.close)]
 for period,g in strategy.groupby(strategy.date.dt.to_period("M")):monthly.setdefault(str(period),{})["QQQ strategy"]=round(float(100*g.net_pnl.sum()),2)
 for period,g in sp500.groupby(sp500.date.dt.to_period("M")):monthly.setdefault(str(period),{})["S&P strategy"]=round(float(100*g.net_pnl.sum()),2)
 for (symbol,period),g in bench.groupby(["symbol",bench.date.dt.to_period("M")]):monthly.setdefault(str(period),{})[symbol]=round(float(100*(g.close.iloc[-1]/g.open.iloc[0]-1)),2)
 labels={p:(pd.Period(p).start_time.strftime("%b %Y")+(" partial" if p in ("2025-08","2026-08") else "")) for p in monthly}
 payload={"title":"QQQ dual-MA top-3 vs QQQ and SPY — last year","subtitle":"Cumulative return from Aug 11, 2025 through Aug 10, 2026 · fixed-base additive strategy P&L · exact SIP +2 bp","cutoff":"2026-05-01","series":series,"monthly":[{"period":p,"label":labels[p],**monthly[p]} for p in sorted(monthly)]};(OUT/"last_year_chart_data.json").write_text(json.dumps(payload,separators=(",",":"))+"\n");print(json.dumps({"points":{k:len(v) for k,v in series.items()},"months":payload["monthly"],"ending":{k:v[-1]["value"] for k,v in series.items()}},indent=2))
if __name__=="__main__":main()
