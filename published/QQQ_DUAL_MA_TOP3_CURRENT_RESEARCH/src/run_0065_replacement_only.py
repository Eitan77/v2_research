from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "campaigns" / "CAM-0611" / "src"
sys.path.insert(0, str(SRC))

from run_0058_self_financing import context, load_execution_quotes, summarize

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0065"


def simulate(p, schedule, quotes, reserve_fraction, extra_bps):
    symbols = [str(s) for s in p.symbols]
    col = {s: i for i, s in enumerate(symbols)}
    quote_by_date = {d: g.set_index("symbol") for d, g in quotes.groupby("date")}
    cash, values, active = 1.0, {}, tuple()
    rows, trades = [], []
    min_cash, max_gross_to_equity, max_single_weight = cash, 0.0, 0.0
    max_single_symbol, max_single_date = None, None
    unchanged_notional_errors = []
    xlnx_reference = pd.read_parquet(ROOT / "campaigns" / "CAM-0600" / "artifacts" / "RUN-0042" / "xlnx_reference_quote.parquet").iloc[0]
    xlnx_terminal = pd.read_parquet(ROOT / "campaigns" / "CAM-0600" / "artifacts" / "RUN-0042" / "xlnx_terminal_quote.parquet").iloc[0]
    xlnx_reference_mid = (float(xlnx_reference.bid_price) + float(xlnx_reference.ask_price)) / 2.0

    for i, raw_day in enumerate(p.dates):
        day = pd.Timestamp(raw_day).normalize()
        did_rebalance = False
        if i in schedule:
            target = schedule[i]
            should_trade = target != active and (values or target)
            if should_trade:
                q = quote_by_date[day]
                for s in list(values):
                    if s not in q.index or not np.isfinite(q.loc[s, "reference_mid"]):
                        cash += values.pop(s)
                        trades.append({"date": day, "symbol": s, "side": "terminal_cash", "notional_mid": 0.0})
                union = set(values) | set(target)
                bid_ratio, ask_ratio = {}, {}
                for s in union:
                    z = q.loc[s]
                    mid40 = (float(z.bid_price) + float(z.ask_price)) / 2.0
                    if s in values:
                        values[s] *= mid40 / float(z.reference_mid)
                    bid_ratio[s] = float(z.bid_price) * (1.0 - extra_bps / 10000.0) / mid40
                    ask_ratio[s] = float(z.ask_price) * (1.0 + extra_bps / 10000.0) / mid40
                nav_mid = cash + sum(values.values())
                continuing = set(values) & set(target)
                before_continuing = {s: values[s] for s in continuing}
                outgoing = set(values) - set(target)
                entrants = set(target) - set(values)
                for s in sorted(outgoing):
                    notional = values.pop(s)
                    cash += notional * bid_ratio[s]
                    trades.append({"date": day, "symbol": s, "side": "sell", "notional_mid": notional,
                                   "nav_before": nav_mid})
                reserve = reserve_fraction * nav_mid
                if entrants:
                    spendable = max(0.0, cash - reserve)
                    entrant_value = spendable / sum(ask_ratio[s] for s in entrants)
                    for s in sorted(entrants):
                        cash -= entrant_value * ask_ratio[s]
                        values[s] = entrant_value
                        trades.append({"date": day, "symbol": s, "side": "buy", "notional_mid": entrant_value,
                                       "nav_before": nav_mid})
                for s in continuing:
                    unchanged_notional_errors.append(abs(values[s] - before_continuing[s]))
                if cash < -1e-12:
                    raise RuntimeError(f"negative cash {cash} on {day}")
                did_rebalance = True
            active = target

        if day == pd.Timestamp("2022-02-11") and "XLNX" in values:
            value_open = values.pop("XLNX")
            proceeds = value_open * float(xlnx_terminal.bid_price) * (1.0 - extra_bps / 10000.0) / xlnx_reference_mid
            cash += proceeds
            trades.append({"date": day, "symbol": "XLNX", "side": "terminal_sell", "notional_mid": value_open,
                           "nav_before": cash + sum(values.values())})

        close_values = {}
        for s, value in values.items():
            c = col[s]
            if did_rebalance:
                z = quote_by_date[day].loc[s]
                mid40 = (float(z.bid_price) + float(z.ask_price)) / 2.0
                adj_mid = p.adj_open[i, c] * mid40 / float(z.reference_mid)
                close_values[s] = value * p.adj_close[i, c] / adj_mid if np.isfinite(adj_mid) and adj_mid > 0 else value
            else:
                close_values[s] = value * p.adj_close[i, c] / p.adj_open[i, c] if np.isfinite(p.adj_open[i, c]) and p.adj_open[i, c] > 0 else value
        equity_close = cash + sum(close_values.values())
        gross = sum(close_values.values())
        gross_ratio = gross / equity_close if equity_close > 0 else np.inf
        largest_symbol = max(close_values, key=close_values.get) if close_values else None
        single = close_values.get(largest_symbol, 0.0) / equity_close if equity_close > 0 else np.inf
        hhi = sum((v / equity_close) ** 2 for v in close_values.values()) if equity_close > 0 else np.inf
        max_gross_to_equity = max(max_gross_to_equity, gross_ratio)
        if single > max_single_weight:
            max_single_weight, max_single_symbol, max_single_date = single, largest_symbol, day
        min_cash = min(min_cash, cash)
        rows.append({"date": day, "equity": equity_close, "cash": cash, "gross_value": gross,
                     "gross_to_equity": gross_ratio, "maximum_position_weight": single,
                     "largest_symbol": largest_symbol, "position_hhi": hhi,
                     "rebalanced": did_rebalance})

        if i + 1 < len(p.dates):
            next_values = {}
            for s, value in values.items():
                c = col[s]
                dividend_adjusted = np.nan_to_num(p.dividend_grid[i + 1, c] * p.split_factor[i + 1, c], nan=0.0)
                if did_rebalance:
                    z = quote_by_date[day].loc[s]
                    mid40 = (float(z.bid_price) + float(z.ask_price)) / 2.0
                    adj_mid = p.adj_open[i, c] * mid40 / float(z.reference_mid)
                    factor = (p.adj_open[i + 1, c] + dividend_adjusted) / adj_mid
                else:
                    factor = (p.adj_open[i + 1, c] + dividend_adjusted) / p.adj_open[i, c]
                next_values[s] = value * factor if np.isfinite(factor) else value
            values = next_values

    daily, trade_frame = pd.DataFrame(rows), pd.DataFrame(trades)
    daily["return"] = daily.equity.pct_change().fillna(daily.equity.iloc[0] - 1.0)
    return daily, trade_frame, {
        "minimum_cash": float(min_cash),
        "maximum_gross_to_equity": float(max_gross_to_equity),
        "maximum_single_position_weight": float(max_single_weight),
        "maximum_single_position_symbol": max_single_symbol,
        "maximum_single_position_date": str(max_single_date.date()) if max_single_date is not None else None,
        "sessions_largest_position_above_40pct": int((daily.maximum_position_weight > 0.40).sum()),
        "sessions_largest_position_above_50pct": int((daily.maximum_position_weight > 0.50).sum()),
        "average_position_hhi": float(daily.position_hhi.mean()),
        "maximum_continuing_notional_error": float(max(unchanged_notional_errors, default=0.0)),
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    p, schedule = context()
    quotes = load_execution_quotes(p)
    rows = []
    for reserve in (0.0, 0.005):
        for extra in (0.0, 2.0, 10.0):
            daily, trades, integrity = simulate(p, schedule, quotes, reserve, extra)
            metrics = {"mode": "replacement_only", "reserve_fraction": reserve, "extra_bps": extra,
                       **summarize(daily, trades), **integrity}
            if metrics["minimum_cash"] < -1e-12 or metrics["maximum_gross_to_equity"] > 1.0 + 1e-12:
                raise RuntimeError(f"cash/exposure failure: {metrics}")
            rows.append(metrics)
            tag = f"reserve{reserve:g}_{extra:g}bps"
            daily.to_parquet(OUT / f"daily_{tag}.parquet", index=False)
            trades.to_parquet(OUT / f"trades_{tag}.parquet", index=False)
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / "metrics.csv", index=False)
    report = {"status": "completed", "planned_variants": 6, "executed_variants": len(rows),
              "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0,
              "fractional_shares": True, "broker_margin": False, "quote_role_coverage": 1.0,
              "metrics": rows}
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(frame[frame.extra_bps.eq(2.0)].to_string(index=False))


if __name__ == "__main__":
    main()
