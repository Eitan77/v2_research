from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0600" / "src"))

from run_0033_exit_overlays import base_context

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0058"
NY = ZoneInfo("America/New_York")
KEYS = ["symbol", "target_ts", "role"]


def context():
    p, _, _, sig, base, _, _ = base_context()
    if str(pd.Timestamp(p.dates.max()).date()) != "2026-04-30":
        raise RuntimeError("discovery cutoff breached")
    if int(p.readiness.get("holdout_rows_loaded_total", 0)) != 0:
        raise RuntimeError("holdout rows loaded")
    schedule = {}
    for signal_i in sig:
        execution_i = int(signal_i) + 1
        if execution_i >= len(p.dates):
            continue
        chosen = tuple(sorted(str(p.symbols[c]) for c in np.flatnonzero(base[int(signal_i)] > 1e-12)))
        schedule[execution_i] = chosen
    return p, schedule


def build_roles():
    OUT.mkdir(parents=True, exist_ok=True)
    p, schedule = context()
    previous: tuple[str, ...] = tuple()
    records = {"0930": [], "0940": []}
    schedule_rows = []
    for i, target in schedule.items():
        day = pd.Timestamp(p.dates[i])
        union = sorted(set(previous) | set(target))
        schedule_rows.append({"date": day, "date_index": i, "target_symbols": list(target), "previous_symbols": list(previous)})
        for symbol in union:
            for label, clock in (("0930", (9, 30)), ("0940", (9, 40))):
                records[label].append({
                    "symbol": symbol,
                    "target_ts": pd.Timestamp(datetime.combine(day.date(), time(*clock), tzinfo=NY)).tz_convert("UTC"),
                    "role": "entry_ask_after",
                })
        previous = target
    for label, rows in records.items():
        frame = pd.DataFrame(rows).drop_duplicates(KEYS).sort_values(["target_ts", "symbol"])
        frame.to_parquet(OUT / f"roles_{label}.parquet", index=False)
    (OUT / "schedule.json").write_text(json.dumps(schedule_rows, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "rebalance_dates": len(schedule), "roles_0930": len(records["0930"]),
                      "roles_0940": len(records["0940"]), "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0}, indent=2))


def quote_frame(label: str):
    frames = []
    local = OUT / f"local_{label}.parquet"
    if local.exists():
        frames.append(pd.read_parquet(local))
    for seconds in (5, 30, 120, 1200):
        remote = OUT / f"remote_{label}_{seconds}s.parquet"
        if remote.exists():
            frames.append(pd.read_parquet(remote))
    if not frames:
        raise RuntimeError(f"no quote frames for {label}")
    q = pd.concat(frames, ignore_index=True)
    q["target_ts"] = pd.to_datetime(q.target_ts, utc=True)
    q["quote_ts"] = pd.to_datetime(q.quote_ts, utc=True)
    valid = q.quote_ts.notna() & q.bid_price.notna() & q.ask_price.notna() & (q.bid_price > 0) & (q.ask_price >= q.bid_price)
    q = q[valid].sort_values("quote_ts").drop_duplicates(KEYS)
    return q


def missing():
    for label in ("0930", "0940"):
        roles = pd.read_parquet(OUT / f"roles_{label}.parquet")
        roles["target_ts"] = pd.to_datetime(roles.target_ts, utc=True)
        q = quote_frame(label)
        merged = roles.merge(q[KEYS], on=KEYS, how="left", indicator=True)
        absent = merged.loc[merged._merge.eq("left_only"), KEYS]
        absent.to_parquet(OUT / f"missing_{label}.parquet", index=False)
        print(label, "roles", len(roles), "missing", len(absent))


def load_execution_quotes(p):
    frames = {}
    for label in ("0930", "0940"):
        roles = pd.read_parquet(OUT / f"roles_{label}.parquet")
        roles["target_ts"] = pd.to_datetime(roles.target_ts, utc=True)
        q = quote_frame(label)
        merged = roles.merge(q[KEYS + ["quote_ts", "bid_price", "ask_price"]], on=KEYS, how="left", validate="one_to_one")
        frames[label] = merged
    q30 = frames["0930"].copy()
    q30["date"] = pd.to_datetime(q30.target_ts, utc=True).dt.tz_convert(NY).dt.tz_localize(None).dt.normalize()
    q30["reference_mid"] = (q30.bid_price + q30.ask_price) / 2.0
    q40 = frames["0940"].copy()
    q40["date"] = pd.to_datetime(q40.target_ts, utc=True).dt.tz_convert(NY).dt.tz_localize(None).dt.normalize()
    merged = q40.merge(q30[["date", "symbol", "reference_mid"]], on=["date", "symbol"], how="left", validate="one_to_one")
    # Historical acquisitions stopped trading before their nominal next rebalance.
    # They are handled as forced terminal cash events in replay rather than as missing Monday roles.
    terminal = merged.symbol.isin(["XLNX", "ALXN"]) & merged.bid_price.isna()
    unresolved = merged[~terminal & (merged.bid_price.isna() | merged.ask_price.isna() | merged.reference_mid.isna())]
    if len(unresolved):
        raise RuntimeError(f"unresolved execution quotes: {len(unresolved)}")
    return merged


def solve_target(cash, current, selected, bid_ratio, ask_ratio, reserve):
    if not selected:
        proceeds = sum(current.get(s, 0.0) * bid_ratio[s] for s in current)
        return 0.0, cash + proceeds

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
        mid = (lo + hi) / 2.0
        if ending_cash(mid) >= reserve:
            lo = mid
        else:
            hi = mid
    return lo, ending_cash(lo)


def simulate(p, schedule, quotes, *, mode: str, reserve_fraction: float, extra_bps: float):
    symbols = [str(s) for s in p.symbols]
    col = {s: i for i, s in enumerate(symbols)}
    quote_by_date = {d: g.set_index("symbol") for d, g in quotes.groupby("date")}
    cash = 1.0
    values: dict[str, float] = {}
    active_target: tuple[str, ...] = tuple()
    rows = []
    trades = []
    min_cash = cash
    max_gross_to_equity = 0.0
    xlnx_reference = pd.read_parquet(ROOT / "campaigns" / "CAM-0600" / "artifacts" / "RUN-0042" / "xlnx_reference_quote.parquet").iloc[0]
    xlnx_terminal = pd.read_parquet(ROOT / "campaigns" / "CAM-0600" / "artifacts" / "RUN-0042" / "xlnx_terminal_quote.parquet").iloc[0]
    xlnx_reference_mid = (float(xlnx_reference.bid_price) + float(xlnx_reference.ask_price)) / 2.0

    for i, day in enumerate(p.dates):
        day = pd.Timestamp(day).normalize()
        did_rebalance = False
        if i in schedule:
            target = schedule[i]
            should_trade = mode == "weekly_equalize" or target != active_target
            if not values and not target:
                should_trade = False
            if should_trade:
                q = quote_by_date[day]
                # Terminal names without a Monday quote are converted to cash at their last marked value.
                for s in list(values):
                    if s not in q.index or not np.isfinite(q.loc[s, "reference_mid"]):
                        cash += values.pop(s)
                        trades.append({"date": day, "symbol": s, "side": "terminal_cash", "notional_mid": 0.0})
                union = set(values) | set(target)
                raw_mid = {}
                bid_ratio = {}
                ask_ratio = {}
                for s in union:
                    row = q.loc[s]
                    raw_mid[s] = float(row.reference_mid)
                    mid40 = (float(row.bid_price) + float(row.ask_price)) / 2.0
                    open_ratio = mid40 / raw_mid[s]
                    if s in values:
                        values[s] *= open_ratio
                    bid_ratio[s] = float(row.bid_price) * (1.0 - extra_bps / 10000.0) / mid40
                    ask_ratio[s] = float(row.ask_price) * (1.0 + extra_bps / 10000.0) / mid40
                nav_mid = cash + sum(values.values())
                reserve = reserve_fraction * nav_mid
                target_value, new_cash = solve_target(cash, values, set(target), bid_ratio, ask_ratio, reserve)
                for s in union:
                    now = values.get(s, 0.0)
                    want = target_value if s in target else 0.0
                    if abs(want - now) > 1e-14:
                        trades.append({"date": day, "symbol": s, "side": "buy" if want > now else "sell",
                                       "notional_mid": abs(want - now), "nav_before": nav_mid})
                values = {s: target_value for s in target if target_value > 0}
                cash = new_cash
                if cash < -1e-12:
                    raise RuntimeError(f"negative cash {cash} on {day}")
                did_rebalance = True
            active_target = target

        # XLNX stopped trading after 2022-02-11 because of its acquisition.
        # Force liquidation at the final valid regular-session SIP bid rather
        # than carrying a fabricated Monday mark.
        if day == pd.Timestamp("2022-02-11") and "XLNX" in values:
            value_open = values.pop("XLNX")
            proceeds = value_open * float(xlnx_terminal.bid_price) * (1.0 - extra_bps / 10000.0) / xlnx_reference_mid
            cash += proceeds
            trades.append({"date": day, "symbol": "XLNX", "side": "terminal_sell", "notional_mid": value_open,
                           "nav_before": cash + sum(values.values())})

        # Mark current state at the close. Rebalance-day values are at 09:40 midpoint; otherwise at the open.
        close_values = {}
        for s, value in values.items():
            c = col[s]
            if did_rebalance:
                row = quote_by_date[day].loc[s]
                mid40 = (float(row.bid_price) + float(row.ask_price)) / 2.0
                adj_mid = p.adj_open[i, c] * mid40 / float(row.reference_mid)
                close_values[s] = value * p.adj_close[i, c] / adj_mid if np.isfinite(adj_mid) and adj_mid > 0 else value
            else:
                close_values[s] = value * p.adj_close[i, c] / p.adj_open[i, c] if np.isfinite(p.adj_open[i, c]) and p.adj_open[i, c] > 0 else value
        equity_close = cash + sum(close_values.values())
        gross = sum(close_values.values())
        max_gross_to_equity = max(max_gross_to_equity, gross / equity_close if equity_close > 0 else np.inf)
        min_cash = min(min_cash, cash)
        rows.append({"date": day, "equity": equity_close, "cash": cash, "gross_value": gross,
                     "gross_to_equity": gross / equity_close if equity_close > 0 else np.nan, "rebalanced": did_rebalance})

        if i + 1 < len(p.dates):
            next_values = {}
            for s, value in values.items():
                c = col[s]
                dividend_adjusted = np.nan_to_num(p.dividend_grid[i + 1, c] * p.split_factor[i + 1, c], nan=0.0)
                if did_rebalance:
                    row = quote_by_date[day].loc[s]
                    mid40 = (float(row.bid_price) + float(row.ask_price)) / 2.0
                    adj_mid = p.adj_open[i, c] * mid40 / float(row.reference_mid)
                    factor = (p.adj_open[i + 1, c] + dividend_adjusted) / adj_mid
                else:
                    factor = (p.adj_open[i + 1, c] + dividend_adjusted) / p.adj_open[i, c]
                next_values[s] = value * factor if np.isfinite(factor) else value
            values = next_values

    daily = pd.DataFrame(rows)
    daily["return"] = daily.equity.pct_change().fillna(daily.equity.iloc[0] - 1.0)
    trade_frame = pd.DataFrame(trades)
    return daily, trade_frame, {"minimum_cash": float(min_cash), "maximum_gross_to_equity": float(max_gross_to_equity)}


def summarize(daily, trades):
    equity = daily.set_index("date").equity
    drawdown = equity / equity.cummax().clip(lower=1.0) - 1.0
    monthly = equity.resample("ME").last().pct_change()
    first_month = equity[equity.index.to_period("M") == equity.index[0].to_period("M")]
    if len(first_month):
        monthly.iloc[0] = first_month.iloc[-1] - 1.0
    yearly = equity.resample("YE").last().pct_change()
    first_year = equity[equity.index.year == equity.index[0].year]
    if len(first_year):
        yearly.iloc[0] = first_year.iloc[-1] - 1.0
    recent = equity.loc[equity.index >= pd.Timestamp("2025-05-01")]
    prior = equity.loc[equity.index < pd.Timestamp("2025-05-01")]
    recent_base = prior.iloc[-1] if len(prior) else 1.0
    recent_return = recent.iloc[-1] / recent_base - 1.0 if len(recent) else 0.0
    trough_date = drawdown.idxmin()
    peak_date = equity.loc[:trough_date].idxmax()
    peak_value = equity.loc[peak_date]
    after_trough = equity.loc[trough_date:]
    recovered = after_trough[after_trough >= peak_value]
    recovery_date = recovered.index[0] if len(recovered) else None
    end_date = recovery_date if recovery_date is not None else equity.index[-1]
    duration_sessions = int(((equity.index >= peak_date) & (equity.index <= end_date)).sum() - 1)
    turnover_fraction = float((trades.notional_mid / trades.nav_before).replace([np.inf, -np.inf], np.nan).fillna(0.0).sum()) if len(trades) else 0.0
    return {
        "ending_equity": float(equity.iloc[-1]),
        "compounded_return": float(equity.iloc[-1] - 1.0),
        "maximum_drawdown": float(-drawdown.min()),
        "maximum_drawdown_peak_date": str(peak_date.date()),
        "maximum_drawdown_trough_date": str(trough_date.date()),
        "maximum_drawdown_recovery_date": str(recovery_date.date()) if recovery_date is not None else None,
        "maximum_drawdown_duration_sessions": duration_sessions,
        "positive_months": int((monthly > 0).sum()),
        "negative_months": int((monthly < 0).sum()),
        "worst_month": float(monthly.min()),
        "worst_year": float(yearly.min()),
        "recent12_compounded_return": float(recent_return),
        "rebalance_sessions": int(daily.rebalanced.sum()),
        "trade_orders": int(len(trades)),
        "turnover_on_starting_capital": float(trades.notional_mid.sum()) if len(trades) else 0.0,
        "sum_trade_notional_over_contemporaneous_nav": turnover_fraction,
    }


def replay():
    OUT.mkdir(parents=True, exist_ok=True)
    p, schedule = context()
    quotes = load_execution_quotes(p)
    rows = []
    for mode in ("weekly_equalize", "change_only"):
        for reserve in (0.0, 0.005):
            for extra in (0.0, 2.0, 10.0):
                daily, trades, integrity = simulate(p, schedule, quotes, mode=mode, reserve_fraction=reserve, extra_bps=extra)
                metrics = {"mode": mode, "reserve_fraction": reserve, "extra_bps": extra, **summarize(daily, trades), **integrity}
                if metrics["minimum_cash"] < -1e-12 or metrics["maximum_gross_to_equity"] > 1.0 + 1e-12:
                    raise RuntimeError(f"cash or exposure gate failed: {metrics}")
                rows.append(metrics)
                tag = f"{mode}_reserve{reserve:g}_{extra:g}bps"
                daily.to_parquet(OUT / f"daily_{tag}.parquet", index=False)
                trades.to_parquet(OUT / f"trades_{tag}.parquet", index=False)
    report = {"status": "completed", "planned_variants": 12, "executed_variants": len(rows),
              "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0,
              "fractional_shares": True, "broker_margin": False, "metrics": rows}
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / "metrics.csv", index=False)
    print(frame[frame.extra_bps.eq(2.0)].to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("roles", "missing", "replay"))
    args = parser.parse_args()
    {"roles": build_roles, "missing": missing, "replay": replay}[args.phase]()
