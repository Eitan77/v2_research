from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
CAM = ROOT / "campaigns"
OUT = CAM / "CAM-0606" / "artifacts" / "RUN-0023"
CATALOG = Path(r"D:\AlgoResearch\data\catalog.duckdb")
sys.path.insert(0, str(CAM / "CAM-0600" / "src"))
sys.path.insert(0, str(CAM / "CAM-0606" / "src"))
from suite_core import load_panels
from run_true_pair_portfolio import pair_trade

VARIANT = "pair_SMH_XLK__r20__zw63__z1.5__corr0.5__stop2"
CLOCKS = {"0930": (9, 30), "0940": (9, 40)}


def target(date, hour, minute):
    return (pd.Timestamp(date).tz_localize("America/New_York") + pd.Timedelta(hours=hour, minutes=minute)).tz_convert("UTC")


def signals():
    panel = load_panels()["etf"]
    data = pair_trade(panel, ("SMH", "XLK"), 20, 63, 1.5, 0.5, 0.02)
    frame = pd.DataFrame({"session_date": pd.DatetimeIndex(panel.dates), "active": data["active"], "direction": data["direction"]})
    return frame[frame.active.eq(1) & frame.session_date.between("2025-05-01", "2026-04-30")].copy()


def prepare_entries():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in signals().itertuples(index=False):
        rich, cheap = (("SMH", "XLK") if item.direction > 0 else ("XLK", "SMH"))
        for clock, (hour, minute) in CLOCKS.items():
            ts = target(item.session_date, hour, minute)
            rows.extend([
                {"clock": clock, "session_date": item.session_date, "symbol": rich, "side": "sell", "leg": "short", "target_ts": ts, "role": "entry_ask_after"},
                {"clock": clock, "session_date": item.session_date, "symbol": cheap, "side": "buy", "leg": "long", "target_ts": ts, "role": "entry_ask_after"},
            ])
    frame = pd.DataFrame(rows)
    if (frame.target_ts >= pd.Timestamp("2026-05-01", tz="UTC")).any():
        raise RuntimeError("holdout entry role")
    frame.to_parquet(OUT / "entry_roles.parquet", index=False)
    print(json.dumps({"active_sessions": int(frame.session_date.nunique()), "entry_roles": len(frame), "holdout_rows_loaded": 0}, indent=2))


def load_best_matches(prefix):
    frames = []
    names = [f"{prefix}_quotes_5s.parquet", f"{prefix}_repair_quotes_5s.parquet", f"{prefix}_quotes_30s.parquet", f"{prefix}_repair_quotes_30s.parquet", f"{prefix}_quotes_120s.parquet"]
    for priority, name in enumerate(names):
        path = OUT / name
        if path.exists():
            x = pd.read_parquet(path)
            if len(x):
                x["priority"] = priority
                frames.append(x)
    if not frames:
        return pd.DataFrame()
    x = pd.concat(frames, ignore_index=True)
    x["target_ts"] = pd.to_datetime(x.target_ts, utc=True)
    x["quote_ts"] = pd.to_datetime(x.quote_ts, utc=True)
    valid = x.bid_price.notna() & x.ask_price.notna() & x.bid_price.gt(0) & x.ask_price.ge(x.bid_price)
    return x[valid].sort_values("priority").drop_duplicates(["symbol", "target_ts", "role"])


def missing(prefix, role_file):
    roles = pd.read_parquet(role_file)
    roles["target_ts"] = pd.to_datetime(roles.target_ts, utc=True)
    found = load_best_matches(prefix)
    keys = found[["symbol", "target_ts", "role"]] if len(found) else pd.DataFrame(columns=["symbol", "target_ts", "role"])
    remain = roles.merge(keys.drop_duplicates(), on=["symbol", "target_ts", "role"], how="left", indicator=True)
    remain = remain[remain._merge.eq("left_only")].drop(columns="_merge")
    remain.to_parquet(OUT / f"{prefix}_missing.parquet", index=False)
    print(json.dumps({"roles": len(roles), "matched": len(roles)-len(remain), "missing": len(remain)}, indent=2))


def prepare_exits():
    entries = pd.read_parquet(OUT / "entry_roles.parquet")
    entries["target_ts"] = pd.to_datetime(entries.target_ts, utc=True)
    quotes = load_best_matches("entry")
    merged = entries.merge(quotes[["symbol", "target_ts", "role", "quote_ts", "bid_price", "ask_price"]], on=["symbol", "target_ts", "role"], how="left", validate="one_to_one")
    if merged.bid_price.isna().any():
        raise RuntimeError(f"entry quotes incomplete: {merged.bid_price.isna().sum()}")
    with duckdb.connect(str(CATALOG), read_only=True) as con:
        bars = con.execute("""
            SELECT symbol,timestamp,high,date
            FROM bars_1m
            WHERE symbol IN ('SMH','XLK')
              AND date BETWEEN DATE '2025-05-01' AND DATE '2026-04-30'
              AND feed='sip' AND adjustment='raw'
        """).df()
    bars["timestamp"] = pd.to_datetime(bars.timestamp, utc=True, format="mixed")
    bars["date"] = pd.to_datetime(bars.date)
    bars = bars.sort_values("timestamp").drop_duplicates(["symbol", "timestamp"], keep="last")
    rows = []
    for (clock, session_date), trade in merged.groupby(["clock", "session_date"], sort=True):
        short = trade[trade.leg.eq("short")].iloc[0]
        long = trade[trade.leg.eq("long")].iloc[0]
        threshold = float(short.bid_price) * 1.02
        regular_close = target(session_date, 15, 59)
        path = bars[(bars.symbol == short.symbol) & (bars.date == pd.Timestamp(session_date)) & (bars.timestamp >= short.target_ts) & (bars.timestamp <= regular_close)]
        hits = path[path.high >= threshold]
        stopped = len(hits) > 0
        if stopped:
            exit_ts = pd.Timestamp(hits.timestamp.iloc[0]) + pd.Timedelta(minutes=1)
            role = "exit_bid_after"
        else:
            exit_ts = regular_close
            role = "exit_bid_before"
        for leg in (short, long):
            rows.append({"clock": clock, "session_date": session_date, "symbol": leg.symbol, "entry_side": leg.side, "leg": leg.leg, "stopped": stopped, "stop_price": threshold if leg.leg == "short" else np.nan, "target_ts": exit_ts, "role": role})
    exits = pd.DataFrame(rows)
    if (exits.target_ts >= pd.Timestamp("2026-05-01", tz="UTC")).any():
        raise RuntimeError("holdout exit role")
    exits.to_parquet(OUT / "exit_roles.parquet", index=False)
    merged.to_parquet(OUT / "entry_fills.parquet", index=False)
    print(json.dumps({"exit_roles": len(exits), "stopped_sessions": int(exits[exits.leg.eq('short')].stopped.sum()), "holdout_rows_loaded": 0}, indent=2))


def max_dd(pnl):
    equity = 1 + pnl.cumsum()
    return float(((equity.cummax() - equity) / equity.cummax()).max())


def replay():
    entries = pd.read_parquet(OUT / "entry_fills.parquet")
    exits = pd.read_parquet(OUT / "exit_roles.parquet")
    exits["target_ts"] = pd.to_datetime(exits.target_ts, utc=True)
    quotes = load_best_matches("exit")
    exits = exits.merge(quotes[["symbol", "target_ts", "role", "quote_ts", "bid_price", "ask_price"]], on=["symbol", "target_ts", "role"], how="left", validate="many_to_one", suffixes=("", "_exit"))
    if exits.bid_price.isna().any():
        raise RuntimeError(f"exit quotes incomplete: {exits.bid_price.isna().sum()}")
    entries = entries.rename(columns={"bid_price": "entry_bid", "ask_price": "entry_ask", "quote_ts": "entry_quote_ts"})
    exits = exits.rename(columns={"bid_price": "exit_bid", "ask_price": "exit_ask", "quote_ts": "exit_quote_ts"})
    fills = entries.merge(exits[["clock", "session_date", "symbol", "leg", "stopped", "stop_price", "exit_quote_ts", "exit_bid", "exit_ask"]], on=["clock", "session_date", "symbol", "leg"], validate="one_to_one")
    rows = []
    daily_frames = []
    for clock, group in fills.groupby("clock"):
        trade = []
        for (session_date,), legs in group.groupby(["session_date"]):
            long = legs[legs.leg.eq("long")].iloc[0]
            short = legs[legs.leg.eq("short")].iloc[0]
            long_return = long.exit_bid / long.entry_ask - 1
            short_exit = max(float(short.exit_ask), float(short.stop_price) * (1 + 0.0005)) if short.stopped else float(short.exit_ask)
            short_return = 1 - short_exit / short.entry_bid
            gross = 0.5 * (long_return + short_return)
            trade.append({"date": pd.Timestamp(session_date), "gross_pnl": gross, "stopped": bool(short.stopped), "long_symbol": long.symbol, "short_symbol": short.symbol})
        trade = pd.DataFrame(trade).set_index("date").sort_index()
        for extra in (0, 2, 5, 10):
            net = trade.gross_pnl - 2 * extra / 10000
            monthly = net.groupby(net.index.to_period("M")).sum().reindex(pd.period_range("2025-05", "2026-04", freq="M"), fill_value=0.0)
            rows.append({"clock": clock, "extra_slippage_bps_per_side": extra, "net_simple_return": float(net.sum()), "maximum_drawdown": max_dd(net), "trades": int(len(net)), "stop_hits": int(trade.stopped.sum()), "green_trades": int((net > 0).sum()), "red_trades": int((net < 0).sum()), "win_rate": float((net > 0).mean()), "positive_months": int((monthly > 0).sum()), "negative_months": int((monthly < 0).sum()), "inactive_months": int((monthly == 0).sum()), "worst_month": float(monthly.min()), "best_month": float(monthly.max())})
            daily_frames.append(pd.DataFrame({"date": net.index, "clock": clock, "extra_slippage_bps_per_side": extra, "gross_pnl": trade.gross_pnl.values, "net_pnl": net.values, "stopped": trade.stopped.values}))
    metrics = pd.DataFrame(rows)
    coverage = min(len(entries), len(exits)) / len(pd.read_parquet(OUT / "entry_roles.parquet"))
    report = {"status": "completed", "run_id": "RUN-0023", "variant": VARIANT, "role_coverage": float(coverage), "metrics": metrics.to_dict("records"), "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0, "broker_margin": False, "direct_short_overnight": False, "interpretation": "Marketable SIP quote replay; stop timestamps use first completed one-minute SIP bar and short cover is floored at stop plus 5 bp."}
    metrics.to_csv(OUT / "quote_metrics.csv", index=False)
    pd.concat(daily_frames, ignore_index=True).to_parquet(OUT / "quote_daily.parquet", index=False)
    fills.to_parquet(OUT / "quote_fills.parquet", index=False)
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    path = CAM / "CAM-0606" / "runs" / "RUN-0023.yaml"
    run = yaml.safe_load(path.read_text(encoding="utf-8")); run["status"] = "completed"; run["result"] = report; run["decision"] = "Judge only after quote metrics, recent path, and execution coverage are reconciled; no overnight short and no promotion from development evidence."; path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    with (CAM / "CAM-0606" / "WORKLOG.jsonl").open("a", encoding="utf-8") as handle: handle.write(json.dumps({"run_id": "RUN-0023", "event": "completed", "result": report}) + "\n")
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("phase", choices=["entries", "entry_missing", "exits", "exit_missing", "replay"]); args = parser.parse_args()
    if args.phase == "entries": prepare_entries()
    elif args.phase == "entry_missing": missing("entry", OUT / "entry_roles.parquet")
    elif args.phase == "exits": prepare_exits()
    elif args.phase == "exit_missing": missing("exit", OUT / "exit_roles.parquet")
    else: replay()


if __name__ == "__main__": main()
