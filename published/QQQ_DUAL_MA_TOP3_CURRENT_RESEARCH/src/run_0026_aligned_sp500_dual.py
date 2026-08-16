from __future__ import annotations

import json
import argparse
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "campaigns" / "CAM-0600" / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0515" / "src"))

import run_0007_sp500_top5 as sp_loader
from baseline_strategies import moving_average
from deep_strategies import active_trend_rank
from suite_core import _panel_from_existing, evaluate_weights, weekly_indices

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0026"
MEMBERSHIP = OUT / "aligned_membership" / "sp500_pit_membership_daily.parquet"
MEMBERSHIP_REPORT = OUT / "aligned_membership" / "membership_report.json"
START = pd.Timestamp("2019-06-21")
CUTOFF = pd.Timestamp("2026-04-30")
HOLDOUT = pd.Timestamp("2026-05-01", tz="UTC")
NY = ZoneInfo("America/New_York")


def utc(day: pd.Timestamp, hhmm: str) -> pd.Timestamp:
    hour, minute = map(int, hhmm.split(":"))
    return pd.Timestamp(datetime.combine(day.date(), time(hour, minute), tzinfo=NY)).tz_convert("UTC")


def build():
    sp_loader.START = START
    sp_loader.MEMBERSHIP_PATH = MEMBERSHIP
    sp_loader.MEMBERSHIP_REPORT_PATH = MEMBERSHIP_REPORT
    data, readiness = sp_loader._build_sp500_data()
    panel = _panel_from_existing("sp500_aligned", data, readiness)
    condition = moving_average(panel, 50) > moving_average(panel, 200)
    weights = active_trend_rank(panel, condition, weekly_indices(panel.dates), 3, "momentum")
    return panel, weights, readiness


def dd(net: pd.Series) -> float:
    equity = 1.0 + net.cumsum()
    return float(((equity.cummax() - equity) / equity.cummax()).max())


def bars_and_ledgers() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    panel, weights, readiness = build()
    metrics = []
    for bps in (0.0, 1.0, 2.0, 5.0, 10.0):
        result, daily, *_ = evaluate_weights(panel, weights, bps, holding="open_to_next_open", execution_lag=1)
        net = daily.net_pnl
        monthly = net.groupby(net.index.to_period("M")).sum()
        metrics.append({
            "bps_per_side": bps,
            "net_simple_return": float(net.sum()),
            "maximum_drawdown": dd(net),
            "positive_months": int((monthly > 0).sum()),
            "negative_months": int((monthly < 0).sum()),
            "worst_month": float(monthly.min()),
            "first_active_date": str(net.index[np.flatnonzero(np.abs(net.to_numpy()) > 1e-15)[0]].date()),
        })
    executed = np.zeros_like(weights)
    executed[1:] = weights[:-1]
    executed = np.where(np.isfinite(panel.adj_open), executed, 0.0)
    previous = np.zeros(panel.n_symbols)
    ledgers = {"0930": [], "0940": []}
    for i, day in enumerate(panel.dates):
        if pd.Timestamp(day) > CUTOFF:
            raise RuntimeError("panel crossed discovery cutoff")
        delta = executed[i] - previous
        for col in np.flatnonzero(np.abs(delta) > 1e-12):
            side = "buy" if delta[col] > 0 else "sell"
            for label, clock in (("0930", "09:30"), ("0940", "09:40")):
                ledgers[label].append({
                    "candidate": "sp500_dual_ma50_200_weekly_top3_aligned",
                    "session_date": pd.Timestamp(day).normalize(),
                    "symbol": str(panel.symbols[col]),
                    "side": side,
                    "delta_weight": float(abs(delta[col])),
                    "target_ts": utc(pd.Timestamp(day), clock),
                    "role": "entry_ask_after" if side == "buy" else "exit_bid_after",
                })
        previous = executed[i].copy()
    ledger_report = {}
    for label, rows in ledgers.items():
        ledger = pd.DataFrame(rows).sort_values(["target_ts", "symbol", "side"])
        if ledger.empty or (pd.to_datetime(ledger.target_ts, utc=True) >= HOLDOUT).any():
            raise RuntimeError("empty ledger or holdout role")
        roles = ledger[["symbol", "target_ts", "role"]].drop_duplicates()
        ledger.to_parquet(OUT / f"ledger_{label}.parquet", index=False)
        roles.to_parquet(OUT / f"roles_{label}.parquet", index=False)
        ledger_report[label] = {"ledger_rows": len(ledger), "unique_roles": len(roles), "symbols": ledger.symbol.nunique()}
    report = {
        "status": "passed",
        "strategy": "unchanged_sp500_dual_ma50_200_weekly_top3_126_skip21",
        "bar_metrics": metrics,
        "readiness": readiness,
        "ledger_report": ledger_report,
        "maximum_loaded_date": str(panel.dates.max().date()),
        "holdout_rows_loaded": 0,
    }
    (OUT / "bar_execution_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"bar_metrics": metrics, "ledger_report": ledger_report, "readiness_summary": {k: readiness[k] for k in ("bars_min_date", "bars_max_date", "membership_min_date", "membership_max_date", "member_pairs_missing_raw_bar", "holdout_rows_loaded_total")}}, indent=2))


def quote_cache(label: str) -> pd.DataFrame:
    paths = [OUT / f"quotes_{label}_5s.parquet", OUT / f"quotes_{label}_30s.parquet"]
    frames = []
    for priority, path in enumerate(paths):
        if path.exists():
            frames.append(pd.read_parquet(path).assign(priority=priority))
    successor = OUT / "quotes_lb_successor_bbwi_30s.parquet"
    if successor.exists():
        frame = pd.read_parquet(successor)
        wanted_minute = 30 if label == "0930" else 40
        frame = frame[pd.to_datetime(frame.target_ts, utc=True).dt.minute.eq(wanted_minute)].copy()
        frame["symbol"] = "LB"
        frames.append(frame.assign(priority=2))
    out = pd.concat(frames, ignore_index=True)
    out["target_ts"] = pd.to_datetime(out.target_ts, utc=True)
    return out.sort_values("priority").drop_duplicates(["symbol", "target_ts", "role"], keep="first")


def replay() -> None:
    panel, weights, _ = build()
    merged = {}
    for label in ("0930", "0940"):
        ledger = pd.read_parquet(OUT / f"ledger_{label}.parquet")
        ledger["target_ts"] = pd.to_datetime(ledger.target_ts, utc=True)
        quotes = quote_cache(label)
        merged[label] = ledger.merge(
            quotes[["symbol", "target_ts", "role", "quote_ts", "bid_price", "ask_price"]],
            on=["symbol", "target_ts", "role"], how="left", validate="one_to_one",
        )
    reference = merged["0930"].copy()
    reference["reference_mid"] = (reference.bid_price + reference.ask_price) / 2.0
    reference = reference[["session_date", "symbol", "side", "reference_mid"]]
    fills = merged["0940"].merge(reference, on=["session_date", "symbol", "side"], how="left", validate="one_to_one")
    fills["complete"] = (
        fills.bid_price.notna() & fills.ask_price.notna() & fills.reference_mid.notna()
        & (fills.bid_price > 0) & (fills.ask_price >= fills.bid_price) & (fills.reference_mid > 0)
    )
    if not fills.complete.all():
        raise RuntimeError(f"incomplete quote roles: {fills.loc[~fills.complete, ['symbol','session_date','side']].to_dict('records')}")
    fills.to_parquet(OUT / "fill_ledger.parquet", index=False)
    _, daily, *_ = evaluate_weights(panel, weights, 0.0, holding="open_to_next_open", execution_lag=1)
    rows, month_rows, year_rows = [], [], []
    for extra in (0.0, 1.0, 2.0, 5.0, 10.0):
        cost = np.asarray(np.where(
            fills.side.eq("buy"),
            fills.delta_weight * (fills.ask_price / fills.reference_mid - 1.0),
            fills.delta_weight * (1.0 - fills.bid_price / fills.reference_mid),
        ) + fills.delta_weight.to_numpy(float) * extra / 10000.0, dtype=float)
        costs = pd.Series(cost, index=pd.to_datetime(fills.session_date)).groupby(level=0).sum()
        net = daily.gross_pnl.subtract(costs, fill_value=0.0)
        monthly = net.groupby(net.index.to_period("M")).sum()
        yearly = net.groupby(net.index.year).sum()
        recent = net.loc[net.index >= pd.Timestamp("2025-05-01")]
        recent_monthly = recent.groupby(recent.index.to_period("M")).sum()
        rows.append({
            "extra_adverse_bps_per_side": extra,
            "net_simple_return": float(net.sum()),
            "maximum_drawdown": dd(net),
            "trade_roles": len(fills),
            "trade_sessions": int(pd.to_datetime(fills.session_date).nunique()),
            "role_coverage": float(fills.complete.mean()),
            "positive_months": int((monthly > 0).sum()),
            "negative_months": int((monthly < 0).sum()),
            "worst_month": float(monthly.min()),
            "worst_year": float(yearly.min()),
            "recent12_net_simple_return": float(recent.sum()),
            "recent12_positive_months": int((recent_monthly > 0).sum()),
            "recent12_negative_months": int((recent_monthly < 0).sum()),
            "recent12_worst_month": float(recent_monthly.min()),
        })
        for period, pnl in monthly.items(): month_rows.append({"extra_bps": extra, "month": str(period), "net_pnl": float(pnl)})
        for year, pnl in yearly.items(): year_rows.append({"extra_bps": extra, "year": int(year), "net_pnl": float(pnl)})
        pd.DataFrame({"date": net.index, "net_pnl": net.values}).to_parquet(OUT / f"daily_quote_{extra:g}bps.parquet", index=False)
    pd.DataFrame(rows).to_csv(OUT / "quote_metrics.csv", index=False)
    pd.DataFrame(month_rows).to_csv(OUT / "monthly_quote_returns.csv", index=False)
    pd.DataFrame(year_rows).to_csv(OUT / "yearly_quote_returns.csv", index=False)
    report = {"status": "completed", "metrics": rows, "quote_contract": "09:30 SIP midpoint to first 09:40 marketable SIP quote", "ticker_transition": "LB exit routed to successor BBWI NBBO on 2021-08-03", "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0, "broker_margin": False}
    (OUT / "quote_execution_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("bars", "replay"), nargs="?", default="bars")
    args = parser.parse_args()
    {"bars": bars_and_ledgers, "replay": replay}[args.phase]()
