from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0611" / "src"))
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0600" / "src"))

from run_0058_self_financing import context, load_execution_quotes, simulate, summarize
from suite_core import evaluate_weights, weekly_indices

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0075"
STATE_PATH = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0074" / "cycles_with_market_state.csv"
BASE_PATH = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0038" / "weights_friday.npy"


def variants(state):
    result = {"control": set()}
    for threshold in (.005, .0075, .01, .0125, .015):
        result[f"QQQ_gap_ge_{threshold:g}"] = set(state.loc[state.QQQ_gap >= threshold, "execution_date"])
    result["SPY_gap_ge_0.01"] = set(state.loc[state.SPY_gap >= .01, "execution_date"])
    result["QQQ_to_0940_ge_0.01"] = set(state.loc[state.QQQ_prior_close_to_0940 >= .01, "execution_date"])
    result["breadth_median_gap_ge_0.01"] = set(state.loc[state.constituent_gap_median >= .01, "execution_date"])
    return result


def gated_weights(p, base, blocked):
    w = base.copy()
    sig = weekly_indices(p.dates)
    for k, s in enumerate(sig):
        e = int(s + 1)
        if e >= len(p.dates):
            continue
        if pd.Timestamp(p.dates[e]) in blocked:
            end = int(sig[k + 1] - 1) if k + 1 < len(sig) else len(p.dates) - 1
            w[int(s):end + 1] = 0.0
    return w


def exact_fixed(p, weights, quotes, extra_bps=2.0):
    _, daily, *_ = evaluate_weights(p, weights, 0.0, holding="open_to_next_open", execution_lag=1)
    executed = np.zeros_like(weights)
    executed[1:] = weights[:-1]
    executed = np.where(np.isfinite(p.adj_open), executed, 0.0)
    q = quotes.set_index(["date", "symbol"])
    costs = pd.Series(0.0, index=pd.DatetimeIndex(p.dates))
    previous = np.zeros(p.n_symbols)
    roles = 0
    for i, day in enumerate(p.dates):
        delta = executed[i] - previous
        for c in np.flatnonzero(np.abs(delta) > 1e-12):
            symbol = str(p.symbols[c]); key = (pd.Timestamp(day), symbol)
            if key not in q.index:
                raise RuntimeError(f"missing exact role {key}")
            row = q.loc[key]
            if symbol == "XLNX" and not np.isfinite(float(row.reference_mid)):
                terminal_root = ROOT / "campaigns" / "CAM-0600" / "artifacts" / "RUN-0042"
                xr = pd.read_parquet(terminal_root / "xlnx_reference_quote.parquet").iloc[0]
                xt = pd.read_parquet(terminal_root / "xlnx_terminal_quote.parquet").iloc[0]
                ref = (float(xr.bid_price) + float(xr.ask_price)) / 2
                friction = 1 - float(xt.bid_price) / ref + extra_bps / 10000
                costs.loc[pd.Timestamp("2022-02-11")] += abs(float(delta[c])) * friction
                roles += 1
                continue
            if delta[c] > 0:
                friction = float(row.ask_price) / float(row.reference_mid) - 1 + extra_bps / 10000
            else:
                friction = 1 - float(row.bid_price) / float(row.reference_mid) + extra_bps / 10000
            costs.iloc[i] += abs(float(delta[c])) * friction
            roles += 1
        previous = executed[i].copy()
    net = daily.gross_pnl - costs
    equity = 1 + net.cumsum(); peak = equity.cummax().clip(lower=1.0); dd = equity / peak - 1
    monthly = net.groupby(net.index.to_period("M")).sum()
    yearly = net.groupby(net.index.year).sum()
    recent = net.loc[net.index >= pd.Timestamp("2025-05-01")]
    metrics = {
        "fixed_return": float(net.sum()), "fixed_maximum_drawdown": float(-dd.min()),
        "fixed_worst_month": float(monthly.min()), "fixed_positive_months": int((monthly > 0).sum()),
        "fixed_worst_year": float(yearly.min()), "fixed_recent12_return": float(recent.sum()),
        "exact_roles": roles, **{f"fixed_{int(y)}": float(v) for y, v in yearly.items()},
    }
    return metrics, net


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    p, schedule = context()
    quotes = load_execution_quotes(p)
    quotes.date = pd.to_datetime(quotes.date)
    state = pd.read_csv(STATE_PATH, parse_dates=["execution_date"])
    base = np.load(BASE_PATH)
    rows = []
    for name, blocked in variants(state).items():
        w = gated_weights(p, base, blocked)
        fixed, fixed_daily = exact_fixed(p, w, quotes)
        gated_schedule = {i: (() if pd.Timestamp(p.dates[i]) in blocked else target) for i, target in schedule.items()}
        daily, trades, integrity = simulate(p, gated_schedule, quotes, mode="change_only",
                                            reserve_fraction=.005, extra_bps=2.0)
        comp = summarize(daily, trades)
        rows.append({
            "variant": name, "blocked_cycles": len(blocked), **fixed,
            "compound_return": comp["compounded_return"], "compound_maximum_drawdown": comp["maximum_drawdown"],
            "compound_worst_month": comp["worst_month"], "compound_positive_months": comp["positive_months"],
            "minimum_cash": integrity["minimum_cash"], "maximum_gross_to_equity": integrity["maximum_gross_to_equity"],
        })
        pd.DataFrame({"date": fixed_daily.index, "net_pnl": fixed_daily.values}).to_parquet(OUT / f"daily_{name}.parquet", index=False)
    result = pd.DataFrame(rows)
    reproduced = float(result.loc[result.variant.eq("control"), "fixed_return"].iloc[0])
    if abs(reproduced - 3.6282963573506333) > 1e-8:
        raise RuntimeError(f"fixed control reconciliation failure {reproduced}")
    result.to_csv(OUT / "metrics.csv", index=False)
    report = {"status": "completed", "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0,
              "quote_role_coverage": 1.0, "broker_margin": False, "metrics": rows}
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
