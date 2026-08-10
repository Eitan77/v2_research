from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "campaigns" / "CAM-0600" / "src"
sys.path.insert(0, str(SRC))

from baseline_strategies import moving_average
from deep_strategies import active_trend_rank
from run_smoothed_corr_ma import build as corr_build
from suite_core import CAMPAIGNS, evaluate_weights, load_panels, month_end_indices, weekly_indices

OUT = CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0042"
RUN = CAMPAIGNS / "CAM-0600" / "runs" / "RUN-0042.yaml"
CUTOFF = pd.Timestamp("2026-04-30")
HOLDOUT = pd.Timestamp("2026-05-01", tz="UTC")
NY = ZoneInfo("America/New_York")
EXTRA_BPS = (0.0, 1.0, 2.0, 5.0, 10.0)


def utc(day: pd.Timestamp, clock: str) -> pd.Timestamp:
    hour, minute = map(int, clock.split(":"))
    return pd.Timestamp(datetime.combine(pd.Timestamp(day).date(), time(hour, minute), tzinfo=NY)).tz_convert("UTC")


def candidates():
    panels = load_panels()
    qqq, sp = panels["qqq"], panels["sp500"]
    qsingle = active_trend_rank(qqq, qqq.adj_close > moving_average(qqq, 150), weekly_indices(qqq.dates), 3, "momentum")
    qdual = active_trend_rank(qqq, moving_average(qqq, 50) > moving_average(qqq, 200), weekly_indices(qqq.dates), 3, "momentum")
    qtriple = active_trend_rank(qqq, (moving_average(qqq, 10) > moving_average(qqq, 50)) & (moving_average(qqq, 50) > moving_average(qqq, 200)), month_end_indices(qqq.dates), 3, "momentum")
    sdual = active_trend_rank(sp, moving_average(sp, 50) > moving_average(sp, 200), weekly_indices(sp.dates), 3, "momentum")
    striple = active_trend_rank(sp, (moving_average(sp, 10) > moving_average(sp, 50)) & (moving_average(sp, 50) > moving_average(sp, 200)), month_end_indices(sp.dates), 3, "momentum")
    scorr = corr_build(sp, 0.8, 3, None)
    return {
        "qqq_single_ma150_weekly_top3": (qqq, qsingle),
        "sp500_dual_ma50_200_weekly_top3": (sp, sdual),
        "sp500_triple_ma10_50_200_monthly_top3": (sp, striple),
        "sp500_daily_ma200_corr08_smooth3_top10": (sp, scorr),
        "qqq_dual_ma50_200_weekly_top3": (qqq, qdual),
        "qqq_triple_ma10_50_200_monthly_top3": (qqq, qtriple),
    }


def make_ledgers() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    selected = candidates()
    reports = []
    for clock in ("09:30", "09:40"):
        rows = []
        for name, (panel, weights) in selected.items():
            executed = np.zeros_like(weights)
            executed[1:] = weights[:-1]
            executed = np.where(np.isfinite(panel.adj_open), executed, 0.0)
            previous = np.zeros(panel.n_symbols)
            for i, day in enumerate(panel.dates):
                if pd.Timestamp(day) > CUTOFF:
                    raise RuntimeError("panel crossed discovery cutoff")
                current = executed[i]
                delta = current - previous
                for col in np.flatnonzero(np.abs(delta) > 1e-12):
                    side = "buy" if delta[col] > 0 else "sell"
                    rows.append({
                        "candidate": name,
                        "session_date": pd.Timestamp(day).normalize(),
                        "symbol": str(panel.symbols[col]),
                        "side": side,
                        "delta_weight": float(abs(delta[col])),
                        "target_ts": utc(pd.Timestamp(day), clock),
                        "role": "entry_ask_after" if side == "buy" else "exit_bid_after",
                    })
                previous = current.copy()
        label = clock.replace(":", "")
        ledger = pd.DataFrame(rows).sort_values(["target_ts", "candidate", "symbol", "side"])
        if ledger.empty or (pd.to_datetime(ledger.target_ts, utc=True) >= HOLDOUT).any():
            raise RuntimeError("empty ledger or holdout role")
        roles = ledger[["symbol", "target_ts", "role"]].drop_duplicates().sort_values(["target_ts", "symbol", "role"])
        ledger.to_parquet(OUT / f"ledger_{label}.parquet", index=False)
        roles.to_parquet(OUT / f"roles_{label}.parquet", index=False)
        reports.append({"clock": label, "ledger_rows": len(ledger), "unique_roles": len(roles), "candidates": ledger.candidate.nunique(), "symbols": ledger.symbol.nunique()})
    (OUT / "ledger_report.json").write_text(json.dumps({"status": "passed", "reports": reports, "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(reports, indent=2))


def quote_sources(label: str) -> list[Path]:
    paths = list(CAMPAIGNS.glob(f"CAM-*/artifacts/**/quotes_{label}_*s.parquet"))
    return sorted(set(paths))


def quote_cache(label: str) -> pd.DataFrame:
    frames = []
    for path in quote_sources(label):
        try:
            frame = pd.read_parquet(path)
        except Exception:
            continue
        required = {"symbol", "target_ts", "role", "bid_price", "ask_price"}
        if not len(frame) or not required.issubset(frame.columns):
            continue
        seconds = int(path.stem.rsplit("_", 1)[-1][:-1])
        frame = frame.copy()
        frame["priority"] = seconds
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["symbol", "target_ts", "role", "bid_price", "ask_price"])
    quotes = pd.concat(frames, ignore_index=True)
    quotes["target_ts"] = pd.to_datetime(quotes.target_ts, utc=True)
    quotes = quotes.sort_values("priority").drop_duplicates(["symbol", "target_ts", "role"], keep="first")
    return quotes


def write_missing() -> None:
    for label in ("0930", "0940"):
        roles = pd.read_parquet(OUT / f"roles_{label}.parquet")
        roles["target_ts"] = pd.to_datetime(roles.target_ts, utc=True)
        quotes = quote_cache(label)
        merged = roles.merge(quotes[["symbol", "target_ts", "role"]], on=["symbol", "target_ts", "role"], how="left", indicator=True)
        missing = merged.loc[merged._merge.eq("left_only"), ["symbol", "target_ts", "role"]]
        missing.to_parquet(OUT / f"missing_{label}.parquet", index=False)
        print(label, "roles", len(roles), "cached", len(roles) - len(missing), "missing", len(missing))


def make_exception_roles() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    reference = pd.DataFrame([{
        "symbol": "XLNX",
        "target_ts": pd.Timestamp("2022-02-11 14:30:00", tz="UTC"),
        "role": "entry_ask_after",
    }])
    terminal = pd.DataFrame([{
        "symbol": "XLNX",
        "target_ts": pd.Timestamp("2022-02-11 21:00:00", tz="UTC"),
        "role": "exit_bid_before",
    }])
    reference.to_parquet(OUT / "xlnx_reference_role.parquet", index=False)
    terminal.to_parquet(OUT / "xlnx_terminal_role.parquet", index=False)
    print("wrote XLNX last-tradable-session exception roles")


def dd(series: pd.Series) -> float:
    equity = 1.0 + series.cumsum()
    return float(((equity.cummax() - equity) / equity.cummax()).max()) if len(equity) else 0.0


def replay() -> None:
    selected = candidates()
    merged = {}
    for label in ("0930", "0940"):
        ledger = pd.read_parquet(OUT / f"ledger_{label}.parquet")
        ledger["target_ts"] = pd.to_datetime(ledger.target_ts, utc=True)
        quotes = quote_cache(label)
        data = ledger.merge(quotes[["symbol", "target_ts", "role", "quote_ts", "bid_price", "ask_price"]], on=["symbol", "target_ts", "role"], how="left", validate="many_to_one")
        data["complete"] = data.bid_price.notna() & data.ask_price.notna() & (data.bid_price > 0) & (data.ask_price >= data.bid_price)
        merged[label] = data
    reference = merged["0930"].copy()
    reference["reference_mid"] = (reference.bid_price + reference.ask_price) / 2
    reference = reference[["candidate", "session_date", "symbol", "side", "reference_mid", "quote_ts"]].rename(columns={"quote_ts": "reference_quote_ts"})
    execution = merged["0940"].merge(reference, on=["candidate", "session_date", "symbol", "side"], how="left", validate="one_to_one")
    execution["fill_source"] = "normal_0940_after"
    exception_mask = execution.symbol.eq("XLNX") & execution.session_date.eq(pd.Timestamp("2022-02-14")) & execution.side.eq("sell")
    if exception_mask.any():
        ref = pd.read_parquet(OUT / "xlnx_reference_quote.parquet").iloc[0]
        terminal = pd.read_parquet(OUT / "xlnx_terminal_quote.parquet").iloc[0]
        if not (ref.bid_price > 0 and ref.ask_price >= ref.bid_price and terminal.bid_price > 0):
            raise RuntimeError("invalid XLNX terminal exception quotes")
        execution.loc[exception_mask, "reference_mid"] = (float(ref.bid_price) + float(ref.ask_price)) / 2.0
        execution.loc[exception_mask, "bid_price"] = float(terminal.bid_price)
        execution.loc[exception_mask, "ask_price"] = float(terminal.ask_price)
        execution.loc[exception_mask, "quote_ts"] = pd.Timestamp(terminal.quote_ts)
        execution.loc[exception_mask, "reference_quote_ts"] = pd.Timestamp(ref.quote_ts)
        execution.loc[exception_mask, "session_date"] = pd.Timestamp("2022-02-11")
        execution.loc[exception_mask, "complete"] = True
        execution.loc[exception_mask, "fill_source"] = "xlnx_last_tradable_bid_before_close"
    execution["complete_both"] = execution.complete & execution.reference_mid.notna() & (execution.reference_mid > 0)
    execution.to_parquet(OUT / "fill_ledger.parquet", index=False)
    rows, annual_rows, monthly_rows, concentration_rows = [], [], [], []
    for name, group in execution.groupby("candidate", sort=False):
        panel, weights = selected[name]
        _, daily, *_ = evaluate_weights(panel, weights, 0.0, holding="open_to_next_open", execution_lag=1)
        daily.index = pd.to_datetime(daily.index)
        complete = group.complete_both
        fills = group.loc[complete].copy()
        executed = np.zeros_like(weights)
        executed[1:] = weights[:-1]
        executed = np.where(np.isfinite(panel.adj_open), executed, 0.0)
        gross_symbol = executed * np.nan_to_num(panel.open_to_next_open_return, nan=0.0)
        gross_symbol[-1] = executed[-1] * np.nan_to_num(panel.open_to_close_return[-1], nan=0.0)
        for extra in EXTRA_BPS:
            adjustment = np.where(
                fills.side.eq("buy"),
                fills.delta_weight * (fills.ask_price / fills.reference_mid - 1.0),
                fills.delta_weight * (1.0 - fills.bid_price / fills.reference_mid),
            ) + fills.delta_weight * extra / 10000.0
            costs = pd.Series(np.asarray(adjustment), index=pd.to_datetime(fills.session_date)).groupby(level=0).sum()
            net = daily.gross_pnl.subtract(costs, fill_value=0.0)
            monthly = net.groupby(net.index.to_period("M")).sum()
            annual = net.groupby(net.index.year).sum()
            recent = net.loc[net.index >= pd.Timestamp("2025-05-01")]
            recent_monthly = recent.groupby(recent.index.to_period("M")).sum()
            symbol_cost = pd.Series(np.asarray(adjustment), index=fills.symbol).groupby(level=0).sum()
            symbol_gross = pd.Series(gross_symbol.sum(axis=0), index=panel.symbols.astype(str))
            symbol_net = symbol_gross.subtract(symbol_cost, fill_value=0.0).sort_values(ascending=False)
            positive = symbol_net.clip(lower=0)
            top5_share = float(positive.head(5).sum() / positive.sum()) if positive.sum() > 0 else None
            leave_top5 = float(net.sum() - symbol_net.head(5).sum())
            rows.append({
                "candidate": name,
                "extra_adverse_bps_per_side": extra,
                "minimum_date": str(net.index.min().date()),
                "maximum_date": str(net.index.max().date()),
                "net_simple_return": float(net.sum()),
                "maximum_drawdown": dd(net),
                "role_coverage": float(complete.mean()),
                "trade_roles": int(len(group)),
                "trade_sessions": int(pd.to_datetime(fills.session_date).nunique()),
                "positive_months": int((monthly > 0).sum()),
                "negative_months": int((monthly < 0).sum()),
                "inactive_months": int((monthly.abs() <= 1e-12).sum()),
                "worst_month": float(monthly.min()),
                "best_month": float(monthly.max()),
                "recent12_net_simple_return": float(recent.sum()),
                "recent12_maximum_drawdown": dd(recent),
                "recent12_positive_months": int((recent_monthly > 0).sum()),
                "recent12_negative_months": int((recent_monthly < 0).sum()),
                "recent12_worst_month": float(recent_monthly.min()),
                "top5_symbol_positive_share": top5_share,
                "leave_top5_return": leave_top5,
                "best_symbol": str(symbol_net.index[0]),
                "best_symbol_pnl": float(symbol_net.iloc[0]),
            })
            for period, pnl in monthly.items(): monthly_rows.append({"candidate": name, "extra_adverse_bps_per_side": extra, "month": str(period), "net_pnl": float(pnl)})
            for year, pnl in annual.items(): annual_rows.append({"candidate": name, "extra_adverse_bps_per_side": extra, "year": int(year), "net_pnl": float(pnl)})
            for symbol, pnl in symbol_net.items(): concentration_rows.append({"candidate": name, "extra_adverse_bps_per_side": extra, "symbol": symbol, "net_pnl": float(pnl)})
            pd.DataFrame({"date": net.index, "net_pnl": net.values}).to_parquet(OUT / f"daily_{name}_{extra:g}bps.parquet", index=False)
    metrics = pd.DataFrame(rows)
    metrics.to_csv(OUT / "quote_metrics.csv", index=False)
    pd.DataFrame(monthly_rows).to_csv(OUT / "monthly_returns.csv", index=False)
    pd.DataFrame(annual_rows).to_csv(OUT / "annual_returns.csv", index=False)
    pd.DataFrame(concentration_rows).to_csv(OUT / "symbol_returns.csv", index=False)
    status = "completed" if len(metrics) and metrics.role_coverage.min() == 1.0 else "blocked_incomplete_quotes"
    report = {"status": status, "run_id": "RUN-0042", "candidate_count": int(metrics.candidate.nunique()), "metrics": metrics.to_dict("records"), "execution_exception": {"symbol": "XLNX", "scheduled_exit": "2022-02-14", "actual_fill_session": "2022-02-11", "fill": "last valid SIP bid before regular close", "affected_candidates": 2, "rows": int(exception_mask.sum())}, "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0, "broker_margin": False}
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    record = yaml.safe_load(RUN.read_text(encoding="utf-8"))
    record["status"] = "completed" if status == "completed" else "failed"
    record["result"] = {"status": status, "candidate_count": report["candidate_count"], "minimum_role_coverage": float(metrics.role_coverage.min()) if len(metrics) else 0.0, "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0, "artifact": "artifacts/RUN-0042/execution_report.json"}
    record["decision"] = "Interpret only after all six candidates have complete full-history reference and execution quote coverage."
    RUN.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    print(metrics.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("ledgers", "missing", "exception_roles", "replay"))
    args = parser.parse_args()
    {"ledgers": make_ledgers, "missing": write_missing, "exception_roles": make_exception_roles, "replay": replay}[args.phase]()
