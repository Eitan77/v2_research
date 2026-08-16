from __future__ import annotations

import argparse
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0059"
PREV = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0058"
NY = ZoneInfo("America/New_York")
END = pd.Timestamp("2026-08-14")
SYMBOLS = ["AMAT", "ARM", "INTC", "LRCX", "MU", "SNDK", "STX", "WDC"]
CHANGE_DATES = {
    pd.Timestamp("2026-05-18"): ("SNDK", "STX", "WDC"),
    pd.Timestamp("2026-06-15"): ("INTC", "MU", "SNDK"),
    pd.Timestamp("2026-06-22"): ("INTC", "SNDK", "WDC"),
    pd.Timestamp("2026-06-29"): ("MU", "SNDK", "WDC"),
    pd.Timestamp("2026-07-27"): ("INTC", "MU", "SNDK"),
    pd.Timestamp("2026-08-10"): ("ARM", "MU", "SNDK"),
}


def env():
    result = {}
    for line in (ROOT / ".env.local").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip("\"'")
    return result


def headers_and_base():
    e = env()
    headers = {
        "APCA-API-KEY-ID": e["ALPACA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": e["ALPACA_API_SECRET_KEY"],
    }
    return headers, e.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/")


def pull():
    OUT.mkdir(parents=True, exist_ok=True)
    headers, base = headers_and_base()
    params = {
        "symbols": ",".join(SYMBOLS), "timeframe": "1Day",
        "start": "2026-04-30T00:00:00Z", "end": "2026-08-15T00:00:00Z",
        "adjustment": "all", "feed": "sip", "sort": "asc", "limit": 10000,
    }
    response = requests.get(base + "/v2/stocks/bars", headers=headers, params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()
    rows = []
    for symbol, bars in payload.get("bars", {}).items():
        for bar in bars:
            rows.append({"symbol": symbol, "date": pd.Timestamp(bar["t"]).tz_convert(NY).tz_localize(None).normalize(),
                         "open": bar["o"], "close": bar["c"], "volume": bar["v"]})
    daily = pd.DataFrame(rows)
    if daily.empty or daily.date.max() != END or (daily.date > END).any():
        raise RuntimeError(f"daily boundary failure: {daily.date.min() if len(daily) else None} to {daily.date.max() if len(daily) else None}")
    daily.to_parquet(OUT / "daily_adjusted.parquet", index=False)

    quote_rows = []
    previous = ("MU", "SNDK", "WDC")
    for day, target in CHANGE_DATES.items():
        for symbol in sorted(set(previous) | set(target)):
            for label, clock in (("0930", (9, 30)), ("0940", (9, 40))):
                target_ts = pd.Timestamp(datetime.combine(day.date(), time(*clock), tzinfo=NY)).tz_convert("UTC")
                qparams = {
                    "start": target_ts.isoformat(),
                    "end": (target_ts + pd.Timedelta(seconds=120)).isoformat(),
                    "feed": "sip", "limit": 10000, "sort": "asc",
                }
                qresponse = requests.get(base + f"/v2/stocks/{symbol}/quotes", headers=headers, params=qparams, timeout=30)
                qresponse.raise_for_status()
                quotes = qresponse.json().get("quotes") or []
                valid = [q for q in quotes if q.get("bp", 0) > 0 and q.get("ap", 0) >= q.get("bp", 0)]
                if not valid:
                    raise RuntimeError(f"missing quote {day.date()} {label} {symbol}")
                q = valid[0]
                quote_rows.append({"date": day, "label": label, "symbol": symbol, "target_ts": target_ts,
                                   "quote_ts": pd.Timestamp(q["t"]), "bid_price": q["bp"], "ask_price": q["ap"]})
        previous = target
    quotes = pd.DataFrame(quote_rows)
    quotes.to_parquet(OUT / "quotes.parquet", index=False)
    report = {
        "status": "passed", "daily_minimum_date": str(daily.date.min().date()),
        "daily_maximum_date": str(daily.date.max().date()), "rows_after_authorized_end": int((daily.date > END).sum()),
        "symbols_requested": len(SYMBOLS), "symbols_returned": int(daily.symbol.nunique()),
        "quote_roles": len(quotes), "quote_coverage": 1.0, "credentials_recorded": False,
    }
    (OUT / "pull_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def solve_target(cash, current, selected, bid_ratio, ask_ratio, reserve):
    def ending_cash(target):
        value = cash
        for symbol in set(current) | set(selected):
            now = current.get(symbol, 0.0)
            want = target if symbol in selected else 0.0
            if want < now:
                value += (now - want) * bid_ratio[symbol]
            elif want > now:
                value -= (want - now) * ask_ratio[symbol]
        return value
    lo, hi = 0.0, (cash + sum(current.values())) / len(selected)
    for _ in range(100):
        mid = (lo + hi) / 2
        if ending_cash(mid) >= reserve:
            lo = mid
        else:
            hi = mid
    return lo, ending_cash(lo)


def terminal_state():
    import sys
    sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0611" / "src"))
    from run_0058_self_financing import context, load_execution_quotes
    p, _ = context()
    daily = pd.read_parquet(PREV / "daily_change_only_reserve0.005_2bps.parquet")
    row = daily[pd.to_datetime(daily.date).eq(pd.Timestamp("2026-04-27"))].iloc[0]
    quotes = load_execution_quotes(p)
    q = quotes[pd.to_datetime(quotes.date).eq(pd.Timestamp("2026-04-27"))].set_index("symbol")
    col = {str(s): i for i, s in enumerate(p.symbols)}
    idx = int(np.flatnonzero(pd.DatetimeIndex(p.dates) == pd.Timestamp("2026-04-27"))[0])
    target = ("MU", "SNDK", "WDC")
    close_ratios = {}
    for symbol in target:
        z = q.loc[symbol]
        mid40 = (float(z.bid_price) + float(z.ask_price)) / 2
        adj_mid = p.adj_open[idx, col[symbol]] * mid40 / float(z.reference_mid)
        close_ratios[symbol] = p.adj_close[idx, col[symbol]] / adj_mid
    target_value = float(row.gross_value) / sum(close_ratios.values())
    values_apr30_open = {}
    idx30 = int(np.flatnonzero(pd.DatetimeIndex(p.dates) == pd.Timestamp("2026-04-30"))[0])
    for symbol in target:
        z = q.loc[symbol]
        mid40 = (float(z.bid_price) + float(z.ask_price)) / 2
        adj_mid = p.adj_open[idx, col[symbol]] * mid40 / float(z.reference_mid)
        values_apr30_open[symbol] = target_value * p.adj_open[idx30, col[symbol]] / adj_mid
    apr30_close = float(daily.iloc[-1].equity)
    cash = float(daily.iloc[-1].cash)
    reconstructed = cash + sum(values_apr30_open[s] * p.adj_close[idx30, col[s]] / p.adj_open[idx30, col[s]] for s in target)
    if abs(reconstructed / apr30_close - 1) > 1e-10:
        raise RuntimeError(f"terminal state mismatch {reconstructed} vs {apr30_close}")
    return cash, values_apr30_open, apr30_close


def replay():
    OUT.mkdir(parents=True, exist_ok=True)
    daily = pd.read_parquet(OUT / "daily_adjusted.parquet")
    daily["date"] = pd.to_datetime(daily.date)
    quotes = pd.read_parquet(OUT / "quotes.parquet")
    quotes["date"] = pd.to_datetime(quotes.date)
    quote_lookup = {(z.date, z.label, z.symbol): z for z in quotes.itertuples()}
    px = {(z.date, z.symbol): z for z in daily.itertuples()}
    dates = pd.DatetimeIndex(sorted(daily.loc[daily.date.between("2026-05-01", END), "date"].unique()))
    cash, values, apr30_equity = terminal_state()
    active = ("MU", "SNDK", "WDC")
    for symbol in active:
        values[symbol] *= float(px[(dates[0], symbol)].open) / float(px[(pd.Timestamp("2026-04-30"), symbol)].open)

    rows, trades = [], []
    min_cash, max_gross = cash, 0.0
    for i, day in enumerate(dates):
        did_rebalance = day in CHANGE_DATES
        if did_rebalance:
            target = CHANGE_DATES[day]
            union = set(values) | set(target)
            bid_ratio, ask_ratio, mid40s = {}, {}, {}
            for symbol in union:
                q30 = quote_lookup[(day, "0930", symbol)]
                q40 = quote_lookup[(day, "0940", symbol)]
                reference = (float(q30.bid_price) + float(q30.ask_price)) / 2
                mid40 = (float(q40.bid_price) + float(q40.ask_price)) / 2
                values[symbol] = values.get(symbol, 0.0) * mid40 / reference
                mid40s[symbol] = mid40
                bid_ratio[symbol] = float(q40.bid_price) * (1 - 0.0002) / mid40
                ask_ratio[symbol] = float(q40.ask_price) * (1 + 0.0002) / mid40
            nav_mid = cash + sum(values.values())
            target_value, new_cash = solve_target(cash, values, set(target), bid_ratio, ask_ratio, 0.005 * nav_mid)
            for symbol in union:
                now, want = values.get(symbol, 0.0), target_value if symbol in target else 0.0
                if abs(want - now) > 1e-14:
                    trades.append({"date": day, "symbol": symbol, "side": "buy" if want > now else "sell",
                                   "notional_mid": abs(want - now), "nav_before": nav_mid})
            values = {symbol: target_value for symbol in target}
            cash, active = new_cash, target

        close_values = {}
        for symbol, value in values.items():
            bar = px[(day, symbol)]
            if did_rebalance:
                q30 = quote_lookup[(day, "0930", symbol)]
                reference = (float(q30.bid_price) + float(q30.ask_price)) / 2
                adj_mid = float(bar.open) * mid40s[symbol] / reference
                close_values[symbol] = value * float(bar.close) / adj_mid
            else:
                close_values[symbol] = value * float(bar.close) / float(bar.open)
        equity = cash + sum(close_values.values())
        gross = sum(close_values.values())
        rows.append({"date": day, "equity": equity, "cash": cash, "gross_value": gross,
                     "gross_to_equity": gross / equity, "rebalanced": did_rebalance})
        min_cash = min(min_cash, cash)
        max_gross = max(max_gross, gross / equity)

        if i + 1 < len(dates):
            next_day = dates[i + 1]
            next_values = {}
            for symbol, value in values.items():
                bar, nxt = px[(day, symbol)], px[(next_day, symbol)]
                if did_rebalance:
                    q30 = quote_lookup[(day, "0930", symbol)]
                    reference = (float(q30.bid_price) + float(q30.ask_price)) / 2
                    adj_mid = float(bar.open) * mid40s[symbol] / reference
                    next_values[symbol] = value * float(nxt.open) / adj_mid
                else:
                    next_values[symbol] = value * float(nxt.open) / float(bar.open)
            values = next_values

    result = pd.DataFrame(rows)
    prior = pd.DataFrame([{"date": pd.Timestamp("2026-04-30"), "equity": apr30_equity}])
    combined = pd.concat([prior, result[["date", "equity"]]], ignore_index=True).set_index("date").equity
    result["return"] = result.equity.pct_change().fillna(result.equity.iloc[0] / apr30_equity - 1)
    monthly = (1 + result.set_index("date")["return"]).groupby(result.date.dt.to_period("M").to_numpy()).prod() - 1
    drawdown = combined / combined.cummax() - 1
    report = {
        "status": "completed", "configuration": "change_only_reserve0.005_2bps",
        "authorized_window": {"start": "2026-05-01", "end": "2026-08-14"},
        "maximum_loaded_date": str(result.date.max().date()), "rows_after_authorized_end": int((result.date > END).sum()),
        "starting_equity": apr30_equity, "ending_equity": float(result.equity.iloc[-1]),
        "oos_compounded_return": float(result.equity.iloc[-1] / apr30_equity - 1),
        "monthly_compounded_returns": {str(period): float(value) for period, value in monthly.items()},
        "oos_maximum_drawdown": float(-drawdown.min()), "oos_maximum_drawdown_trough": str(drawdown.idxmin().date()),
        "rebalance_sessions": int(result.rebalanced.sum()), "trade_orders": len(trades),
        "minimum_cash": float(min_cash), "maximum_gross_to_equity": float(max_gross),
        "quote_roles": len(quotes), "quote_coverage": 1.0, "broker_margin_used": False,
        "august_is_partial_through": "2026-08-14",
    }
    if min_cash < -1e-12 or max_gross > 1 + 1e-12:
        raise RuntimeError("cash or exposure gate failed")
    result.to_parquet(OUT / "oos_daily.parquet", index=False)
    pd.DataFrame(trades).to_parquet(OUT / "oos_trades.parquet", index=False)
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("pull", "replay"))
    args = parser.parse_args()
    {"pull": pull, "replay": replay}[args.phase]()
