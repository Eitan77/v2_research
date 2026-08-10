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
OUT = CAM / "CAM-0613" / "artifacts" / "RUN-0023"
CATALOG = Path(r"D:\AlgoResearch\data\catalog.duckdb")
sys.path.insert(0, str(CAM / "CAM-0600" / "src"))
sys.path.insert(0, str(CAM / "CAM-0613" / "src"))
from baseline_strategies import pivot_weights
from deep_strategies import liquid_mask, trend_mask
from run_ranked_pivot_setups import select_weights
from suite_core import load_panels

CLOCKS = {"0930": (9, 30), "0940": (9, 40)}


def target(date, hour, minute):
    return (pd.Timestamp(date).tz_localize("America/New_York") + pd.Timedelta(hours=hour, minutes=minute)).tz_convert("UTC")


def build_orders():
    OUT.mkdir(parents=True, exist_ok=True)
    panel = load_panels()["sp500"]
    _, _, _ = pivot_weights(panel, "long")
    prev_high = np.vstack([np.full(panel.n_symbols, np.nan), panel.raw_high[:-1]])
    prev_low = np.vstack([np.full(panel.n_symbols, np.nan), panel.raw_low[:-1]])
    prev_close = np.vstack([np.full(panel.n_symbols, np.nan), panel.raw_close[:-1]])
    pivot = (prev_high + prev_low + prev_close) / 3
    resistance = 2 * pivot - prev_low
    prior_range = prev_high - prev_low
    score = (resistance - panel.raw_open) / prior_range
    valid = panel.member & np.isfinite(panel.raw_open) & np.isfinite(pivot) & np.isfinite(resistance) & (panel.raw_open > pivot) & (resistance > panel.raw_open) & liquid_mask(panel, 0.35) & trend_mask(panel, 100)
    weights = select_weights(score, valid, 10)
    rows = []
    for i, date in enumerate(panel.dates):
        if date < pd.Timestamp("2025-05-01") or date > pd.Timestamp("2026-04-30"):
            continue
        for col in np.flatnonzero(weights[i] > 0):
            for clock, (hour, minute) in CLOCKS.items():
                rows.append({"clock": clock, "session_date": pd.Timestamp(date), "symbol": str(panel.symbols[col]), "weight": float(weights[i, col]), "daily_open": float(panel.raw_open[i, col]), "pivot": float(pivot[i, col]), "resistance": float(resistance[i, col]), "entry_target_ts": target(date, hour, minute)})
    orders = pd.DataFrame(rows)
    orders.to_parquet(OUT / "orders.parquet", index=False)
    keys = orders[["session_date", "symbol"]].drop_duplicates()
    keys.to_parquet(OUT / "order_keys.parquet", index=False)
    temp_dir = Path(r"D:\AlgoResearch\data\temp\CAM-0613_RUN-0023")
    temp_dir.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(CATALOG), read_only=True) as con:
        con.execute("SET threads=16")
        con.execute(f"SET temp_directory='{temp_dir.as_posix()}'")
        con.execute("SET memory_limit='20GB'")
        con.execute("SET max_temp_directory_size='20GB'")
        con.execute("SET preserve_insertion_order=false")
        bar_frames = []
        keyed = keys.copy()
        keyed["month"] = keyed.session_date.dt.to_period("M")
        for month, month_keys in keyed.groupby("month", sort=True):
            start = month.start_time.date().isoformat()
            end = min(month.end_time.date(), pd.Timestamp("2026-04-30").date()).isoformat()
            symbols = ",".join("'" + str(x).replace("'", "''") + "'" for x in sorted(month_keys.symbol.unique()))
            chunk = con.execute(f"""
                SELECT symbol,timestamp,open,high,low,close,date
                FROM read_parquet('D:/AlgoResearch/data/raw/alpaca/market/stocks/bars_1m/**/*.parquet', union_by_name=true, hive_partitioning=true)
                WHERE date BETWEEN DATE '{start}' AND DATE '{end}'
                  AND symbol IN ({symbols})
                  AND feed='sip' AND adjustment='raw'
                QUALIFY row_number() OVER (
                  PARTITION BY symbol,timestamp,timeframe,feed,adjustment
                  ORDER BY COALESCE(TRY_CAST(ingested_at AS TIMESTAMP), TIMESTAMP '1900-01-01') DESC,
                           COALESCE(source_ingestion_id,'') DESC
                )=1
            """).df()
            allowed = set(zip(month_keys.symbol.astype(str), month_keys.session_date.dt.date))
            chunk = chunk[[((str(s), pd.Timestamp(d).date()) in allowed) for s, d in zip(chunk.symbol, chunk.date)]]
            bar_frames.append(chunk)
        bars = pd.concat(bar_frames, ignore_index=True)
    bars["timestamp"] = pd.to_datetime(bars.timestamp, utc=True, format="mixed")
    bars["date"] = pd.to_datetime(bars.date)
    bars = bars.sort_values("timestamp").drop_duplicates(["symbol", "timestamp"], keep="last")
    bars.to_parquet(OUT / "order_minute_bars.parquet", index=False)
    write_exits(orders, bars)
    exits = pd.read_parquet(OUT / "orders_with_exits.parquet")
    report = {"orders": int(len(orders)), "sessions": int(orders.session_date.nunique()), "symbols": int(orders.symbol.nunique()), "orders_per_session": float(len(orders)/orders.session_date.nunique()), "target_touch_rate": float(exits.target_touched.mean()), "minute_rows": int(len(bars)), "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0}
    (OUT / "order_readiness.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def write_exits(orders, bars):
    with duckdb.connect(str(CATALOG), read_only=True) as con:
        calendar = con.execute("""
            SELECT DISTINCT TRY_CAST(date AS DATE) session_date, close
            FROM calendar
            WHERE TRY_CAST(date AS DATE) BETWEEN DATE '2025-05-01' AND DATE '2026-04-30'
        """).df()
    close_map = {pd.Timestamp(row.session_date): tuple(map(int, str(row.close).split(":"))) for row in calendar.itertuples(index=False)}
    bar_groups = {(str(symbol), pd.Timestamp(date)): group for (symbol, date), group in bars.groupby(["symbol", "date"], sort=False)}
    exit_rows = []
    for order in orders.itertuples(index=False):
        close_hour, close_minute = close_map[pd.Timestamp(order.session_date)]
        close_ts = target(order.session_date, close_hour, close_minute) - pd.Timedelta(minutes=1)
        day_bars = bar_groups.get((str(order.symbol), pd.Timestamp(order.session_date)), bars.iloc[0:0])
        path = day_bars[(day_bars.timestamp >= order.entry_target_ts) & (day_bars.timestamp <= close_ts)]
        hits = path[path.high >= order.resistance]
        if len(hits):
            exit_ts = pd.Timestamp(hits.timestamp.iloc[0]) + pd.Timedelta(minutes=1)
            role = "exit_bid_after"
            target_touched = True
        else:
            exit_ts = close_ts
            role = "exit_bid_before"
            target_touched = False
        exit_rows.append({**order._asdict(), "exit_target_ts": exit_ts, "exit_role": role, "target_touched": target_touched})
    exits = pd.DataFrame(exit_rows)
    entries = orders.rename(columns={"entry_target_ts": "target_ts"}).assign(role="entry_ask_after")
    exit_roles = exits.rename(columns={"exit_target_ts": "target_ts", "exit_role": "role"})
    entries.to_parquet(OUT / "entry_roles.parquet", index=False)
    exit_roles.to_parquet(OUT / "exit_roles.parquet", index=False)
    exits.to_parquet(OUT / "orders_with_exits.parquet", index=False)


def repair_exits():
    orders = pd.read_parquet(OUT / "orders.parquet")
    orders["entry_target_ts"] = pd.to_datetime(orders.entry_target_ts, utc=True)
    bars = pd.read_parquet(OUT / "order_minute_bars.parquet")
    bars["timestamp"] = pd.to_datetime(bars.timestamp, utc=True)
    bars["date"] = pd.to_datetime(bars.date)
    write_exits(orders, bars)
    exits = pd.read_parquet(OUT / "orders_with_exits.parquet")
    print(json.dumps({"orders": len(exits), "target_touch_rate": float(exits.target_touched.mean()), "early_close_orders": int(exits.session_date.isin(pd.to_datetime(["2025-07-03","2025-11-28","2025-12-24"])).sum()), "holdout_rows_loaded": 0}, indent=2))


def load_matches(prefix):
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
    x["target_ts"] = pd.to_datetime(x.target_ts, utc=True); x["quote_ts"] = pd.to_datetime(x.quote_ts, utc=True)
    valid = x.bid_price.notna() & x.ask_price.notna() & x.bid_price.gt(0) & x.ask_price.ge(x.bid_price)
    return x[valid].sort_values("priority").drop_duplicates(["symbol", "target_ts", "role"])


def missing(prefix):
    role_path = OUT / ("entry_roles.parquet" if prefix == "entry" else "exit_roles.parquet")
    roles = pd.read_parquet(role_path); roles["target_ts"] = pd.to_datetime(roles.target_ts, utc=True)
    found = load_matches(prefix)
    keys = found[["symbol", "target_ts", "role"]] if len(found) else pd.DataFrame(columns=["symbol", "target_ts", "role"])
    remain = roles.merge(keys.drop_duplicates(), on=["symbol", "target_ts", "role"], how="left", indicator=True)
    remain = remain[remain._merge.eq("left_only")].drop(columns="_merge")
    remain.to_parquet(OUT / f"{prefix}_missing.parquet", index=False)
    print(json.dumps({"roles": len(roles), "matched": len(roles)-len(remain), "missing": len(remain), "coverage": float(1-len(remain)/len(roles))}, indent=2))


def max_dd(pnl):
    equity = 1 + pnl.cumsum()
    return float(((equity.cummax() - equity) / equity.cummax()).max())


def replay():
    orders = pd.read_parquet(OUT / "orders_with_exits.parquet")
    orders["entry_target_ts"] = pd.to_datetime(orders.entry_target_ts, utc=True); orders["exit_target_ts"] = pd.to_datetime(orders.exit_target_ts, utc=True)
    entries = load_matches("entry"); exits = load_matches("exit")
    fills = orders.merge(entries[["symbol", "target_ts", "role", "quote_ts", "bid_price", "ask_price"]].rename(columns={"target_ts": "entry_target_ts", "role": "entry_role", "quote_ts": "entry_quote_ts", "bid_price": "entry_bid", "ask_price": "entry_ask"}), left_on=["symbol", "entry_target_ts"], right_on=["symbol", "entry_target_ts"], how="left", validate="many_to_one")
    fills = fills.merge(exits[["symbol", "target_ts", "role", "quote_ts", "bid_price", "ask_price"]].rename(columns={"target_ts": "exit_target_ts", "role": "exit_role_match", "quote_ts": "exit_quote_ts", "bid_price": "exit_bid", "ask_price": "exit_ask"}), left_on=["symbol", "exit_target_ts", "exit_role"], right_on=["symbol", "exit_target_ts", "exit_role_match"], how="left", validate="many_to_one")
    complete = fills.entry_ask.notna() & fills.exit_bid.notna()
    if not complete.all():
        raise RuntimeError(f"incomplete quote roles: {(~complete).sum()}")
    bars = pd.read_parquet(OUT / "order_minute_bars.parquet")
    bars["timestamp"] = pd.to_datetime(bars.timestamp, utc=True); bars["date"] = pd.to_datetime(bars.date)
    bar_groups = {(str(symbol), pd.Timestamp(date)): group for (symbol, date), group in bars.groupby(["symbol", "date"], sort=False)}
    results = []
    order_detail = []
    for order in fills.itertuples(index=False):
        day_bars = bar_groups.get((str(order.symbol), pd.Timestamp(order.session_date)), bars.iloc[0:0])
        path = day_bars[day_bars.timestamp >= order.entry_target_ts]
        mid = (order.entry_bid + order.entry_ask) / 2
        checks = {
            "marketable_post_cross": (True, order.entry_ask, order.exit_bid),
            "marketable_target_limit": (True, order.entry_ask, order.resistance if order.target_touched else order.exit_bid),
            "midpoint_touch_5m": (bool((path[path.timestamp <= order.entry_target_ts + pd.Timedelta(minutes=5)].low <= mid).any()), mid, order.resistance if order.target_touched else order.exit_bid),
            "bid_touch_5m": (bool((path[path.timestamp <= order.entry_target_ts + pd.Timedelta(minutes=5)].low <= order.entry_bid).any()), order.entry_bid, order.resistance if order.target_touched else order.exit_bid),
            "midpoint_touch_15m": (bool((path[path.timestamp <= order.entry_target_ts + pd.Timedelta(minutes=15)].low <= mid).any()), mid, order.resistance if order.target_touched else order.exit_bid),
        }
        for execution, (filled, entry_price, exit_price) in checks.items():
            order_detail.append({"clock": order.clock, "session_date": order.session_date, "symbol": order.symbol, "weight": order.weight, "execution": execution, "filled": filled, "target_touched": order.target_touched, "entry_price": entry_price, "exit_price": exit_price, "gross_pnl": order.weight * (exit_price / entry_price - 1) if filled else 0.0})
    detail = pd.DataFrame(order_detail)
    for (clock, execution), group in detail.groupby(["clock", "execution"]):
        for extra in (0, 1, 2, 5):
            x = group.copy(); x["net_pnl"] = x.gross_pnl - x.filled * x.weight * 2 * extra / 10000
            daily = x.groupby(pd.to_datetime(x.session_date)).net_pnl.sum().sort_index()
            monthly = daily.groupby(daily.index.to_period("M")).sum().reindex(pd.period_range("2025-05", "2026-04", freq="M"), fill_value=0.0)
            active = x[x.filled].session_date.nunique()
            results.append({"clock": clock, "execution": execution, "extra_slippage_bps_per_side": extra, "net_simple_return": float(daily.sum()), "maximum_drawdown": max_dd(daily), "orders": int(len(x)), "filled_orders": int(x.filled.sum()), "fill_rate": float(x.filled.mean()), "active_sessions": int(active), "calendar_sessions": int(group.session_date.nunique()), "active_session_fraction": float(active/group.session_date.nunique()), "trades_per_active_session": float(x.filled.sum()/active) if active else 0.0, "green_sessions": int((daily > 0).sum()), "red_sessions": int((daily < 0).sum()), "positive_months": int((monthly > 0).sum()), "negative_months": int((monthly < 0).sum()), "inactive_months": int((monthly == 0).sum()), "monthly_average": float(monthly.mean()), "monthly_median": float(monthly.median()), "worst_month": float(monthly.min()), "best_month": float(monthly.max())})
    metrics = pd.DataFrame(results)
    report = {"status": "completed", "run_id": "RUN-0023", "variant": "sp500__pivot_target_room_over_prior_range__top10__sma100__vol0", "quote_role_coverage": 1.0, "metrics": metrics.to_dict("records"), "limit_fill_warning": "Touch-based fills are optimistic upper bounds without queue position; marketable_post_cross is the conservative control.", "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0, "broker_margin": False, "direct_short": False}
    metrics.to_csv(OUT / "quote_execution_metrics.csv", index=False); detail.to_parquet(OUT / "quote_order_detail.parquet", index=False); fills.to_parquet(OUT / "quote_fills.parquet", index=False); (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    path = CAM / "CAM-0613" / "runs" / "RUN-0023.yaml"; run = yaml.safe_load(path.read_text(encoding="utf-8")); run["status"] = "completed"; run["result"] = report; run["decision"] = "Use marketable fills as primary; passive touch variants are diagnostics only until queue-aware paper fills exist."; path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    with (CAM / "CAM-0613" / "WORKLOG.jsonl").open("a", encoding="utf-8") as handle: handle.write(json.dumps({"run_id": "RUN-0023", "event": "completed", "result": report}) + "\n")
    print(metrics.to_string(index=False))


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("phase", choices=["orders", "repair_exits", "entry_missing", "exit_missing", "replay"]); args = parser.parse_args()
    if args.phase == "orders": build_orders()
    elif args.phase == "repair_exits": repair_exits()
    elif args.phase == "entry_missing": missing("entry")
    elif args.phase == "exit_missing": missing("exit")
    else: replay()


if __name__ == "__main__": main()
