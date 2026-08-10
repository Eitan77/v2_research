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

from run_notable_neighborhoods import variants
from run_suite import _load_or_build_fundamentals
from suite_core import CAMPAIGNS, evaluate_weights, load_panels

OUT = CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0036"
RUN = CAMPAIGNS / "CAM-0600" / "runs" / "RUN-0036.yaml"
START = pd.Timestamp("2025-05-01")
END = pd.Timestamp("2026-04-30")
NY = ZoneInfo("America/New_York")
PRIOR_DIRS = [
    CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0031",
    CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0034",
    CAMPAIGNS / "CAM-0610" / "artifacts" / "RUN-0025",
    CAMPAIGNS / "CAM-0610" / "artifacts" / "RUN-0027",
    CAMPAIGNS / "CAM-0610" / "artifacts" / "RUN-0029",
    CAMPAIGNS / "CAM-0617" / "artifacts" / "RUN-0027",
]
CHOICES = {
    "ma200_top5_native_p5": ("ma200_uncapped", "ma200_top5_histnative_p5"),
    "ma200_top5_history252_p5": ("ma200_uncapped", "ma200_top5_hist252_p5"),
    "corr09_p3": ("ma200_corr_capped", "corr0.9_p3"),
    "ma50_200_top5_p5": ("ma50_200", "ma50_200_top5_p5"),
    "cluster_r10_top3_p1": ("cluster_residual", "r10_top3_p1"),
    "characteristic_r5_top10_p3": ("characteristic_residual", "r5_top10_p3"),
    "triple_ma10_50_200_top3": ("triple_ma", "ma10_50_200_top3_vtnone"),
    "alpha_turnover_band_20": ("true_daily_alpha", "ivnone_band0.2"),
}


def utc(day, clock):
    hour, minute = map(int, clock.split(":"))
    return pd.Timestamp(datetime.combine(pd.Timestamp(day).date(), time(hour, minute), tzinfo=NY)).tz_convert("UTC")


def selected():
    panels = load_panels()
    fundamentals, _ = _load_or_build_fundamentals(panels)
    wanted = set(CHOICES.values())
    found = {}
    for family, variant, panel, weights in variants(panels, fundamentals):
        key = (family, variant)
        if key in wanted:
            label = next(name for name, value in CHOICES.items() if value == key)
            found[label] = (panel, weights)
    assert set(found) == set(CHOICES), set(CHOICES) - set(found)
    return found


def make_ledgers():
    OUT.mkdir(parents=True, exist_ok=True)
    for clock in ("09:30", "09:40"):
        rows = []
        for name, (panel, weights) in selected().items():
            executed = np.zeros_like(weights)
            executed[1:] = weights[:-1]
            previous = np.zeros(panel.n_symbols)
            for i, day in enumerate(panel.dates):
                day = pd.Timestamp(day).normalize()
                current = executed[i]
                if day < START:
                    previous = current.copy()
                    continue
                if day > END:
                    break
                delta = current - previous
                for col in np.flatnonzero(np.abs(delta) > 1e-8):
                    side = "buy" if delta[col] > 0 else "sell"
                    rows.append({
                        "candidate": name,
                        "session_date": day,
                        "symbol": str(panel.symbols[col]),
                        "side": side,
                        "delta_weight": float(abs(delta[col])),
                        "target_ts": utc(day, clock),
                        "role": "entry_ask_after" if side == "buy" else "exit_bid_after",
                    })
                previous = current.copy()
        label = clock.replace(":", "")
        ledger = pd.DataFrame(rows)
        ledger.to_parquet(OUT / f"ledger_{label}.parquet", index=False)
        ledger[["symbol", "target_ts", "role"]].drop_duplicates().to_parquet(OUT / f"roles_{label}.parquet", index=False)
        print(label, len(ledger), ledger.candidate.nunique())


def quote_cache(label):
    frames = []
    for directory in [OUT, *PRIOR_DIRS]:
        for seconds in (5, 30, 120, 300, 1200):
            path = directory / f"quotes_{label}_{seconds}s.parquet"
            if path.exists() and path.stat().st_size:
                frame = pd.read_parquet(path)
                if len(frame):
                    frame["priority"] = seconds
                    frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["symbol", "target_ts", "role", "bid_price", "ask_price"])
    quotes = pd.concat(frames, ignore_index=True).sort_values("priority").drop_duplicates(["symbol", "target_ts", "role"])
    quotes.target_ts = pd.to_datetime(quotes.target_ts, utc=True)
    return quotes


def write_missing():
    for label in ("0930", "0940"):
        roles = pd.read_parquet(OUT / f"roles_{label}.parquet")
        roles.target_ts = pd.to_datetime(roles.target_ts, utc=True)
        quotes = quote_cache(label)
        merged = roles.merge(quotes[["symbol", "target_ts", "role"]], on=["symbol", "target_ts", "role"], how="left", indicator=True)
        missing = merged[merged._merge == "left_only"][["symbol", "target_ts", "role"]]
        missing.to_parquet(OUT / f"missing_{label}.parquet", index=False)
        print(label, "roles", len(roles), "missing", len(missing))


def drawdown(series):
    equity = 1.0 + series.cumsum()
    return float(((equity.cummax() - equity) / equity.cummax()).max())


def replay():
    merged = {}
    for label in ("0930", "0940"):
        ledger = pd.read_parquet(OUT / f"ledger_{label}.parquet")
        ledger.target_ts = pd.to_datetime(ledger.target_ts, utc=True)
        quotes = quote_cache(label)
        data = ledger.merge(quotes[["symbol", "target_ts", "role", "bid_price", "ask_price"]], on=["symbol", "target_ts", "role"], how="left", validate="many_to_one")
        data["complete"] = data.bid_price.notna() & data.ask_price.notna() & (data.bid_price > 0) & (data.ask_price >= data.bid_price)
        merged[label] = data
    reference = merged["0930"].copy()
    reference["reference_mid"] = (reference.bid_price + reference.ask_price) / 2
    reference = reference[["candidate", "session_date", "symbol", "side", "reference_mid"]]
    merged["0940"] = merged["0940"].merge(reference, on=["candidate", "session_date", "symbol", "side"], how="left", validate="one_to_one")
    rows = []
    selected_weights = selected()
    for name, group in merged["0940"].groupby("candidate"):
        panel, weights = selected_weights[name]
        _, daily, *_ = evaluate_weights(panel, weights, 0, holding="open_to_next_open", execution_lag=1)
        daily.index = pd.to_datetime(daily.index)
        base = daily[(daily.index >= START) & (daily.index <= END)]
        complete = group.complete & group.reference_mid.notna() & (group.reference_mid > 0)
        fills = group[complete].copy()
        for extra in (0.0, 1.0, 2.0, 5.0):
            adjustment = np.where(
                fills.side.eq("buy"),
                fills.delta_weight * (fills.ask_price / fills.reference_mid - 1),
                fills.delta_weight * (1 - fills.bid_price / fills.reference_mid),
            ) + fills.delta_weight * extra / 10000
            costs = pd.Series(np.asarray(adjustment), index=pd.to_datetime(fills.session_date)).groupby(level=0).sum()
            net = base.gross_pnl.subtract(costs, fill_value=0)
            monthly = net.groupby(net.index.to_period("M")).sum()
            symbol_cost = pd.Series(np.asarray(adjustment), index=fills.symbol).groupby(level=0).sum()
            rows.append({
                "candidate": name,
                "extra_adverse_bps_per_side": extra,
                "net_simple_return": float(net.sum()),
                "maximum_drawdown": drawdown(net),
                "role_coverage": float(complete.mean()),
                "trade_roles": int(len(group)),
                "trade_sessions": int(pd.to_datetime(fills.session_date).nunique()),
                "trade_session_fraction": float(pd.to_datetime(fills.session_date).nunique() / len(base)),
                "positive_months": int((monthly > 0).sum()),
                "negative_months": int((monthly < 0).sum()),
                "monthly_average": float(monthly.mean()),
                "monthly_median": float(monthly.median()),
                "worst_month": float(monthly.min()),
                "best_month": float(monthly.max()),
                "quote_cost_top5_symbol_share": float(symbol_cost.nlargest(5).sum() / symbol_cost.sum()) if symbol_cost.sum() else 0.0,
            })
            net.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT / f"daily_{name}_{extra:g}bps.parquet", index=False)
    metrics = pd.DataFrame(rows)
    metrics.to_csv(OUT / "quote_metrics.csv", index=False)
    report = {
        "status": "completed" if metrics.role_coverage.min() == 1 else "blocked_incomplete_quotes",
        "run_id": "RUN-0036",
        "metrics": metrics.to_dict("records"),
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "broker_margin": False,
    }
    report = json.loads(json.dumps(report, default=lambda x: x.item() if isinstance(x, np.generic) else str(x)))
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n")
    record = yaml.safe_load(RUN.read_text())
    record["status"] = "completed" if report["status"] == "completed" else "failed"
    record["result"] = report
    record["decision"] = "Retain only complete-coverage quote candidates whose improvement survives the strict-history, concentration, cadence, and parameter-neighborhood audits."
    RUN.write_text(yaml.safe_dump(record, sort_keys=False))
    print(metrics[metrics.extra_adverse_bps_per_side.isin([0, 2, 5])].to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["ledgers", "missing", "replay"])
    args = parser.parse_args()
    {"ledgers": make_ledgers, "missing": write_missing, "replay": replay}[args.phase]()
