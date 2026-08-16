from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0611" / "src"))
from run_0058_self_financing import context, load_execution_quotes, solve_target

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0060"
PREV = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0059"
NY = ZoneInfo("America/New_York")
END = pd.Timestamp("2026-08-14")
MAX_REBALANCE_GROSS = 1.0


def env():
    result = {}
    for line in (ROOT / ".env.local").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip("\"'")
    return result


def oos_schedule():
    bars = pd.read_parquet(PREV / "daily_adjusted.parquet")
    bars["date"] = pd.to_datetime(bars.date)
    dates = pd.DatetimeIndex(sorted(bars.loc[bars.date.between("2026-05-01", END), "date"].unique()))
    targets = json.loads((ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0042" / "targets.json").read_text())
    schedule = {}
    for row in targets:
        signal = pd.Timestamp(row["signal_date"])
        later = dates[dates > signal]
        if len(later):
            schedule[later[0]] = tuple(sorted(row["selected"]))
    return bars, dates, schedule


def pull_quotes():
    OUT.mkdir(parents=True, exist_ok=True)
    bars, _, schedule = oos_schedule()
    e = env()
    headers = {"APCA-API-KEY-ID": e["ALPACA_API_KEY_ID"], "APCA-API-SECRET-KEY": e["ALPACA_API_SECRET_KEY"]}
    base = e.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/")
    rows = []
    previous = ("MU", "SNDK", "WDC")
    for day, target in sorted(schedule.items()):
        for symbol in sorted(set(previous) | set(target)):
            for label, clock in (("0930", (9, 30)), ("0940", (9, 40))):
                target_ts = pd.Timestamp(datetime.combine(day.date(), time(*clock), tzinfo=NY)).tz_convert("UTC")
                params = {"start": target_ts.isoformat(), "end": (target_ts + pd.Timedelta(seconds=120)).isoformat(),
                          "feed": "sip", "limit": 10000, "sort": "asc"}
                response = requests.get(base + f"/v2/stocks/{symbol}/quotes", headers=headers, params=params, timeout=30)
                response.raise_for_status()
                quotes = response.json().get("quotes") or []
                valid = [q for q in quotes if q.get("bp", 0) > 0 and q.get("ap", 0) >= q.get("bp", 0)]
                if not valid:
                    raise RuntimeError(f"missing quote {day.date()} {label} {symbol}")
                q = valid[0]
                rows.append({"date": day, "label": label, "symbol": symbol, "target_ts": target_ts,
                             "quote_ts": pd.Timestamp(q["t"]), "bid_price": q["bp"], "ask_price": q["ap"]})
        previous = target
    quotes = pd.DataFrame(rows)
    quotes.to_parquet(OUT / "oos_quotes.parquet", index=False)
    report = {"status": "passed", "schedule_sessions": len(schedule), "quote_roles": len(quotes),
              "quote_coverage": 1.0, "maximum_loaded_date": str(bars.date.max().date()),
              "rows_after_authorized_end": int((bars.date > END).sum()), "credentials_recorded": False}
    (OUT / "pull_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def simulate_discovery():
    p, schedule = context()
    quotes = load_execution_quotes(p)
    symbols = [str(s) for s in p.symbols]
    col = {s: i for i, s in enumerate(symbols)}
    quote_by_date = {d: g.set_index("symbol") for d, g in quotes.groupby("date")}
    cash, values, active = 1.0, {}, tuple()
    rows, trades = [], []
    min_cash, max_gross, max_rebalance_gross = cash, 0.0, 0.0
    xlnx_ref = pd.read_parquet(ROOT / "campaigns" / "CAM-0600" / "artifacts" / "RUN-0042" / "xlnx_reference_quote.parquet").iloc[0]
    xlnx_terminal = pd.read_parquet(ROOT / "campaigns" / "CAM-0600" / "artifacts" / "RUN-0042" / "xlnx_terminal_quote.parquet").iloc[0]
    xlnx_mid = (float(xlnx_ref.bid_price) + float(xlnx_ref.ask_price)) / 2

    for i, raw_day in enumerate(p.dates):
        day = pd.Timestamp(raw_day).normalize()
        rebalanced = False
        if i in schedule:
            target = schedule[i]
            if values or target:
                q = quote_by_date[day]
                for symbol in list(values):
                    if symbol not in q.index or not np.isfinite(q.loc[symbol, "reference_mid"]):
                        cash += values.pop(symbol)
                union = set(values) | set(target)
                bid_ratio, ask_ratio, mids = {}, {}, {}
                for symbol in union:
                    z = q.loc[symbol]
                    mid40 = (float(z.bid_price) + float(z.ask_price)) / 2
                    if symbol in values:
                        values[symbol] *= mid40 / float(z.reference_mid)
                    mids[symbol] = mid40
                    bid_ratio[symbol] = float(z.bid_price) * (1 - 0.0002) / mid40
                    ask_ratio[symbol] = float(z.ask_price) * (1 + 0.0002) / mid40
                nav = cash + sum(values.values())
                locked_profit = max(0.0, nav - 1.0)
                target_value, new_cash = solve_target(cash, values, set(target), bid_ratio, ask_ratio, locked_profit)
                for symbol in union:
                    now, want = values.get(symbol, 0.0), target_value if symbol in target else 0.0
                    if abs(want - now) > 1e-14:
                        trades.append({"date": day, "symbol": symbol, "side": "buy" if want > now else "sell",
                                       "notional_mid": abs(want - now), "nav_before": nav})
                values = {symbol: target_value for symbol in target if target_value > 0}
                max_rebalance_gross = max(max_rebalance_gross, sum(values.values()))
                cash, active, rebalanced = new_cash, target, True

        if day == pd.Timestamp("2022-02-11") and "XLNX" in values:
            value = values.pop("XLNX")
            cash += value * float(xlnx_terminal.bid_price) * (1 - 0.0002) / xlnx_mid

        close_values = {}
        for symbol, value in values.items():
            c = col[symbol]
            if rebalanced:
                z = quote_by_date[day].loc[symbol]
                mid40 = (float(z.bid_price) + float(z.ask_price)) / 2
                adj_mid = p.adj_open[i, c] * mid40 / float(z.reference_mid)
                close_values[symbol] = value * p.adj_close[i, c] / adj_mid
            else:
                close_values[symbol] = value * p.adj_close[i, c] / p.adj_open[i, c] if np.isfinite(p.adj_open[i, c]) else value
        equity, gross = cash + sum(close_values.values()), sum(close_values.values())
        rows.append({"date": day, "equity": equity, "cash": cash, "gross_value": gross,
                     "gross_to_equity": gross / equity if equity else 0.0, "rebalanced": rebalanced})
        min_cash, max_gross = min(min_cash, cash), max(max_gross, gross)

        if i + 1 < len(p.dates):
            next_values = {}
            for symbol, value in values.items():
                c = col[symbol]
                dividend = np.nan_to_num(p.dividend_grid[i + 1, c] * p.split_factor[i + 1, c], nan=0.0)
                if rebalanced:
                    z = quote_by_date[day].loc[symbol]
                    mid40 = (float(z.bid_price) + float(z.ask_price)) / 2
                    adj_mid = p.adj_open[i, c] * mid40 / float(z.reference_mid)
                    factor = (p.adj_open[i + 1, c] + dividend) / adj_mid
                else:
                    factor = (p.adj_open[i + 1, c] + dividend) / p.adj_open[i, c]
                next_values[symbol] = value * factor if np.isfinite(factor) else value
            values = next_values
    return p, pd.DataFrame(rows), pd.DataFrame(trades), cash, values, active, min_cash, max_gross, max_rebalance_gross


def replay():
    OUT.mkdir(parents=True, exist_ok=True)
    p, discovery, discovery_trades, cash, values, active, min_cash, max_gross, max_rebalance_gross = simulate_discovery()
    bars, dates, schedule = oos_schedule()
    quotes = pd.read_parquet(OUT / "oos_quotes.parquet")
    quotes["date"] = pd.to_datetime(quotes.date)
    qlookup = {(z.date, z.label, z.symbol): z for z in quotes.itertuples()}
    px = {(z.date, z.symbol): z for z in bars.itertuples()}
    for symbol in active:
        values[symbol] *= float(px[(dates[0], symbol)].open) / float(px[(pd.Timestamp("2026-04-30"), symbol)].open)
    rows, trades = [], []
    for i, day in enumerate(dates):
        rebalanced = day in schedule
        mids = {}
        if rebalanced:
            target = schedule[day]
            union = set(values) | set(target)
            bid_ratio, ask_ratio = {}, {}
            for symbol in union:
                q30, q40 = qlookup[(day, "0930", symbol)], qlookup[(day, "0940", symbol)]
                ref = (float(q30.bid_price) + float(q30.ask_price)) / 2
                mid40 = (float(q40.bid_price) + float(q40.ask_price)) / 2
                values[symbol] = values.get(symbol, 0.0) * mid40 / ref
                mids[symbol] = (mid40, ref)
                bid_ratio[symbol] = float(q40.bid_price) * (1 - 0.0002) / mid40
                ask_ratio[symbol] = float(q40.ask_price) * (1 + 0.0002) / mid40
            nav = cash + sum(values.values())
            locked_profit = max(0.0, nav - 1.0)
            target_value, new_cash = solve_target(cash, values, set(target), bid_ratio, ask_ratio, locked_profit)
            for symbol in union:
                now, want = values.get(symbol, 0.0), target_value if symbol in target else 0.0
                if abs(want - now) > 1e-14:
                    trades.append({"date": day, "symbol": symbol, "side": "buy" if want > now else "sell",
                                   "notional_mid": abs(want - now), "nav_before": nav})
            values = {symbol: target_value for symbol in target}
            max_rebalance_gross = max(max_rebalance_gross, sum(values.values()))
            cash, active = new_cash, target

        close_values = {}
        for symbol, value in values.items():
            bar = px[(day, symbol)]
            if rebalanced:
                mid40, ref = mids[symbol]
                close_values[symbol] = value * float(bar.close) / (float(bar.open) * mid40 / ref)
            else:
                close_values[symbol] = value * float(bar.close) / float(bar.open)
        equity, gross = cash + sum(close_values.values()), sum(close_values.values())
        rows.append({"date": day, "equity": equity, "cash": cash, "gross_value": gross,
                     "gross_to_equity": gross / equity, "rebalanced": rebalanced})
        min_cash, max_gross = min(min_cash, cash), max(max_gross, gross)
        if i + 1 < len(dates):
            nxt = dates[i + 1]
            next_values = {}
            for symbol, value in values.items():
                bar, next_bar = px[(day, symbol)], px[(nxt, symbol)]
                denominator = float(bar.open)
                if rebalanced:
                    mid40, ref = mids[symbol]
                    denominator = float(bar.open) * mid40 / ref
                next_values[symbol] = value * float(next_bar.open) / denominator
            values = next_values

    oos = pd.DataFrame(rows)
    combined = pd.concat([discovery, oos], ignore_index=True)
    combined["return"] = combined.equity.pct_change().fillna(combined.equity.iloc[0] - 1)
    peak = combined.equity.cummax().clip(lower=1)
    dd = combined.equity / peak - 1
    cutoff_equity = float(discovery.iloc[-1].equity)
    oos["return"] = oos.equity.pct_change().fillna(oos.equity.iloc[0] / cutoff_equity - 1)
    monthly_oos = (1 + oos.set_index("date")["return"]).groupby(oos.date.dt.to_period("M").to_numpy()).prod() - 1
    report = {
        "status": "completed", "configuration": "weekly_fixed_initial_risk_budget_profit_sweep",
        "discovery_ending_equity": cutoff_equity, "full_ending_equity": float(combined.iloc[-1].equity),
        "full_compounded_return": float(combined.iloc[-1].equity - 1),
        "full_maximum_drawdown": float(-dd.min()),
        "post_observation_may_aug_return": float(oos.iloc[-1].equity / cutoff_equity - 1),
        "post_observation_monthly_returns": {str(k): float(v) for k, v in monthly_oos.items()},
        "post_observation_maximum_drawdown": float(-(oos.equity / oos.equity.cummax().clip(lower=cutoff_equity) - 1).min()),
        "ending_cash": float(oos.iloc[-1].cash), "ending_gross": float(oos.iloc[-1].gross_value),
        "minimum_cash": float(min_cash), "maximum_marked_gross_between_rebalances": float(max_gross),
        "maximum_rebalance_target_gross": float(max_rebalance_gross),
        "discovery_orders": int(len(discovery_trades)), "post_observation_orders": int(len(trades)),
        "quote_role_coverage": 1.0, "maximum_loaded_date": str(oos.date.max().date()),
        "rows_after_authorized_end": int((oos.date > END).sum()), "broker_margin_used": False,
        "evidence_label": "May onward is post-holdout-observation diagnostic, not fresh OOS",
    }
    if min_cash < -1e-12 or max_rebalance_gross > MAX_REBALANCE_GROSS + 1e-9:
        raise RuntimeError(f"cash/risk-budget gate failed: {min_cash=} {max_rebalance_gross=}")
    discovery.to_parquet(OUT / "discovery_daily.parquet", index=False)
    oos.to_parquet(OUT / "post_observation_daily.parquet", index=False)
    combined.to_parquet(OUT / "combined_daily.parquet", index=False)
    pd.concat([discovery_trades, pd.DataFrame(trades)], ignore_index=True).to_parquet(OUT / "trades.parquet", index=False)
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("pull_quotes", "replay"))
    args = parser.parse_args()
    {"pull_quotes": pull_quotes, "replay": replay}[args.phase]()
