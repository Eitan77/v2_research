from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
CAM = ROOT / "campaigns"
PARENT = CAM / "CAM-0613" / "artifacts" / "RUN-0023"
OUT = CAM / "CAM-0613" / "artifacts" / "RUN-0024"
CATALOG = Path(r"D:\AlgoResearch\data\catalog.duckdb")
sys.path.insert(0, str(CAM / "CAM-0613" / "src"))
from run_pivot_quote_replay import load_matches, target


def close_roles():
    OUT.mkdir(parents=True, exist_ok=True)
    orders = pd.read_parquet(PARENT / "orders.parquet")
    with duckdb.connect(str(CATALOG), read_only=True) as con:
        calendar = con.execute("""SELECT DISTINCT TRY_CAST(date AS DATE) session_date,close FROM calendar WHERE TRY_CAST(date AS DATE) BETWEEN DATE '2025-05-01' AND DATE '2026-04-30'""").df()
    close_map = {pd.Timestamp(row.session_date): tuple(map(int, str(row.close).split(":"))) for row in calendar.itertuples(index=False)}
    rows = []
    for order in orders.drop_duplicates(["session_date", "symbol"]).itertuples(index=False):
        hour, minute = close_map[pd.Timestamp(order.session_date)]
        rows.append({"session_date": order.session_date, "symbol": order.symbol, "target_ts": target(order.session_date, hour, minute) - pd.Timedelta(minutes=1), "role": "exit_bid_before"})
    frame = pd.DataFrame(rows)
    frame.to_parquet(OUT / "close_roles.parquet", index=False)
    print(json.dumps({"close_roles": len(frame), "sessions": int(frame.session_date.nunique()), "holdout_rows_loaded": 0}, indent=2))


def load_close_matches():
    frames = []
    for priority, name in enumerate(("close_quotes_5s.parquet", "close_quotes_30s.parquet", "close_quotes_120s.parquet")):
        path = OUT / name
        if path.exists():
            x = pd.read_parquet(path)
            if len(x): x["priority"] = priority; frames.append(x)
    if not frames: return pd.DataFrame()
    x = pd.concat(frames, ignore_index=True); x["target_ts"] = pd.to_datetime(x.target_ts, utc=True); x["quote_ts"] = pd.to_datetime(x.quote_ts, utc=True)
    valid = x.bid_price.notna() & x.ask_price.notna() & x.bid_price.gt(0) & x.ask_price.ge(x.bid_price)
    return x[valid].sort_values("priority").drop_duplicates(["symbol", "target_ts", "role"])


def missing():
    roles = pd.read_parquet(OUT / "close_roles.parquet"); roles["target_ts"] = pd.to_datetime(roles.target_ts, utc=True)
    found = load_close_matches(); keys = found[["symbol", "target_ts", "role"]] if len(found) else pd.DataFrame(columns=["symbol", "target_ts", "role"])
    remain = roles.merge(keys, on=["symbol", "target_ts", "role"], how="left", indicator=True); remain = remain[remain._merge.eq("left_only")].drop(columns="_merge"); remain.to_parquet(OUT / "close_missing.parquet", index=False)
    print(json.dumps({"roles": len(roles), "matched": len(roles)-len(remain), "missing": len(remain), "coverage": float(1-len(remain)/len(roles))}, indent=2))


def max_dd(pnl):
    equity = 1 + pnl.cumsum(); return float(((equity.cummax()-equity)/equity.cummax()).max())


def replay():
    orders = pd.read_parquet(PARENT / "orders.parquet"); orders["entry_target_ts"] = pd.to_datetime(orders.entry_target_ts, utc=True)
    entry_quotes = load_matches("entry")
    entries = orders.merge(entry_quotes[["symbol","target_ts","quote_ts","bid_price","ask_price"]].rename(columns={"target_ts":"entry_target_ts","quote_ts":"entry_quote_ts","bid_price":"entry_bid","ask_price":"entry_ask"}), on=["symbol","entry_target_ts"], how="left", validate="many_to_one")
    close = pd.read_parquet(OUT / "close_roles.parquet"); close["target_ts"] = pd.to_datetime(close.target_ts, utc=True)
    cq = load_close_matches(); close = close.merge(cq[["symbol","target_ts","role","quote_ts","bid_price"]], on=["symbol","target_ts","role"], how="left", validate="one_to_one").rename(columns={"bid_price":"close_bid","quote_ts":"close_quote_ts"})
    entries = entries.merge(close[["session_date","symbol","close_bid","close_quote_ts"]], on=["session_date","symbol"], how="left", validate="many_to_one")
    if entries.entry_bid.isna().any() or entries.close_bid.isna().any(): raise RuntimeError("incomplete entry or close quote")
    bars = pd.read_parquet(PARENT / "order_minute_bars.parquet"); bars["timestamp"] = pd.to_datetime(bars.timestamp, utc=True); bars["date"] = pd.to_datetime(bars.date)
    bar_groups = {(str(s),pd.Timestamp(d)):g for (s,d),g in bars.groupby(["symbol","date"],sort=False)}
    details = []
    for order in entries.itertuples(index=False):
        day = bar_groups.get((str(order.symbol),pd.Timestamp(order.session_date)),bars.iloc[0:0]); path = day[day.timestamp >= order.entry_target_ts]
        for buffer in (0,1,2,5):
            entry_level = order.entry_bid * (1-buffer/10000)
            eligible = path[path.timestamp <= order.entry_target_ts+pd.Timedelta(minutes=5)]
            hits = eligible[eligible.low <= entry_level]
            filled = len(hits)>0
            target_filled = False; exit_price = order.close_bid; entry_time = pd.NaT
            if filled:
                entry_bar = hits.iloc[0]; entry_time = pd.Timestamp(entry_bar.timestamp)+pd.Timedelta(minutes=1)
                later = path[path.timestamp >= entry_time]
                target_hits = later[later.high >= order.resistance*(1+buffer/10000)]
                if len(target_hits): target_filled=True; exit_price=order.resistance
            details.append({"clock":order.clock,"session_date":order.session_date,"symbol":order.symbol,"weight":order.weight,"penetration_bps":buffer,"filled":filled,"target_filled":target_filled,"entry_price":order.entry_bid,"exit_price":exit_price,"gross_pnl":order.weight*(exit_price/order.entry_bid-1) if filled else 0.0})
    detail = pd.DataFrame(details); results=[]
    for (clock,buffer),group in detail.groupby(["clock","penetration_bps"]):
        for extra in (0,1,2):
            x=group.copy(); x["net_pnl"]=x.gross_pnl-x.filled*x.weight*2*extra/10000; daily=x.groupby(pd.to_datetime(x.session_date)).net_pnl.sum().sort_index(); monthly=daily.groupby(daily.index.to_period("M")).sum().reindex(pd.period_range("2025-05","2026-04",freq="M"),fill_value=0.0); active=x[x.filled].session_date.nunique(); filled=x[x.filled]
            results.append({"clock":clock,"penetration_bps":buffer,"extra_adverse_bps_per_side":extra,"net_simple_return":float(daily.sum()),"maximum_drawdown":max_dd(daily),"orders":len(x),"filled_orders":int(x.filled.sum()),"fill_rate":float(x.filled.mean()),"target_fill_rate_of_filled":float(filled.target_filled.mean()) if len(filled) else 0.0,"active_sessions":int(active),"calendar_sessions":int(x.session_date.nunique()),"active_session_fraction":float(active/x.session_date.nunique()),"trades_per_active_session":float(x.filled.sum()/active) if active else 0.0,"green_sessions":int((daily>0).sum()),"red_sessions":int((daily<0).sum()),"positive_months":int((monthly>0).sum()),"negative_months":int((monthly<0).sum()),"inactive_months":int((monthly==0).sum()),"monthly_average":float(monthly.mean()),"monthly_median":float(monthly.median()),"worst_month":float(monthly.min()),"best_month":float(monthly.max())})
    metrics=pd.DataFrame(results); report={"status":"completed","run_id":"RUN-0024","metrics":metrics.to_dict("records"),"passive_fill_warning":"Even strict penetration and minute-volume evidence do not prove queue fills; results are an optimistic execution bound.","maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin":False,"direct_short":False}; metrics.to_csv(OUT/"sequential_limit_metrics.csv",index=False); detail.to_parquet(OUT/"sequential_limit_orders.parquet",index=False); (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    path=CAM/"CAM-0613"/"runs"/"RUN-0024.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="completed"; run["result"]=report; run["decision"]="Do not promote from touch evidence; require prospective queue-aware paper fills even if strict penetration remains profitable."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
    with (CAM/"CAM-0613"/"WORKLOG.jsonl").open("a",encoding="utf-8") as handle: handle.write(json.dumps({"run_id":"RUN-0024","event":"completed","result":report})+"\n")
    print(metrics.to_string(index=False))


def main():
    p=argparse.ArgumentParser(); p.add_argument("phase",choices=["close_roles","missing","replay"]); a=p.parse_args()
    if a.phase=="close_roles": close_roles()
    elif a.phase=="missing": missing()
    else: replay()


if __name__=="__main__": main()
