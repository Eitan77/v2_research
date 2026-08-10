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

from run_smoothed_corr_ma import build
from suite_core import CAMPAIGNS, evaluate_weights, load_panels

OUT = CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0039"
RUN = CAMPAIGNS / "CAM-0600" / "runs" / "RUN-0039.yaml"
START, END = pd.Timestamp("2025-05-01"), pd.Timestamp("2026-04-30")
NY = ZoneInfo("America/New_York")
PRIOR = [
    CAMPAIGNS / "CAM-0600" / "artifacts" / x for x in ("RUN-0039", "RUN-0036", "RUN-0034", "RUN-0031")
] + [
    CAMPAIGNS / "CAM-0610" / "artifacts" / x for x in ("RUN-0025", "RUN-0027", "RUN-0029")
]


def utc(day, clock):
    hour, minute = map(int, clock.split(":"))
    return pd.Timestamp(datetime.combine(pd.Timestamp(day).date(), time(hour, minute), tzinfo=NY)).tz_convert("UTC")


def weights():
    panel = load_panels()["sp500"]
    return panel, build(panel, 0.8, 5, None)


def quotes(label):
    frames = []
    for directory in PRIOR:
        for seconds in (5, 30, 120, 300, 1200):
            path = directory / f"quotes_{label}_{seconds}s.parquet"
            if path.exists() and path.stat().st_size:
                frame = pd.read_parquet(path)
                if len(frame):
                    frame["priority"] = seconds
                    frames.append(frame)
    data = pd.concat(frames, ignore_index=True).sort_values("priority").drop_duplicates(["symbol", "target_ts", "role"])
    data.target_ts = pd.to_datetime(data.target_ts, utc=True)
    return data


def ledgers():
    OUT.mkdir(parents=True, exist_ok=True)
    panel, signal = weights()
    executed = np.zeros_like(signal)
    executed[1:] = signal[:-1]
    for clock in ("09:30", "09:40"):
        rows, previous = [], np.zeros(panel.n_symbols)
        for i, day in enumerate(panel.dates):
            day = pd.Timestamp(day).normalize()
            current = executed[i]
            if day < START:
                previous = current.copy()
                continue
            if day > END:
                break
            delta = current - previous
            for column in np.flatnonzero(np.abs(delta) > 1e-8):
                side = "buy" if delta[column] > 0 else "sell"
                rows.append({"session_date": day, "symbol": str(panel.symbols[column]), "side": side,
                             "delta_weight": float(abs(delta[column])), "target_ts": utc(day, clock),
                             "role": "entry_ask_after" if side == "buy" else "exit_bid_after"})
            previous = current.copy()
        label = clock.replace(":", "")
        ledger = pd.DataFrame(rows)
        ledger.to_parquet(OUT / f"ledger_{label}.parquet", index=False)
        ledger[["symbol", "target_ts", "role"]].drop_duplicates().to_parquet(OUT / f"roles_{label}.parquet", index=False)
        print(label, len(ledger))


def missing():
    for label in ("0930", "0940"):
        roles = pd.read_parquet(OUT / f"roles_{label}.parquet")
        roles.target_ts = pd.to_datetime(roles.target_ts, utc=True)
        cache = quotes(label)
        merged = roles.merge(cache[["symbol", "target_ts", "role"]], on=["symbol", "target_ts", "role"], how="left", indicator=True)
        result = merged[merged._merge == "left_only"][["symbol", "target_ts", "role"]]
        result.to_parquet(OUT / f"missing_{label}.parquet", index=False)
        print(label, len(result))


def dd(series):
    equity = 1 + series.cumsum()
    return float(((equity.cummax() - equity) / equity.cummax()).max())


def replay():
    filled = {}
    for label in ("0930", "0940"):
        ledger = pd.read_parquet(OUT / f"ledger_{label}.parquet")
        ledger.target_ts = pd.to_datetime(ledger.target_ts, utc=True)
        cache = quotes(label)
        filled[label] = ledger.merge(cache[["symbol", "target_ts", "role", "bid_price", "ask_price"]], on=["symbol", "target_ts", "role"], how="left", validate="one_to_one")
    reference = filled["0930"].copy()
    reference["reference_mid"] = (reference.bid_price + reference.ask_price) / 2
    data = filled["0940"].merge(reference[["session_date", "symbol", "side", "reference_mid"]], on=["session_date", "symbol", "side"], validate="one_to_one")
    complete = data.bid_price.notna() & data.ask_price.notna() & data.reference_mid.notna()
    panel, signal = weights()
    _, daily, *_ = evaluate_weights(panel, signal, 0, holding="open_to_next_open", execution_lag=1)
    daily.index = pd.to_datetime(daily.index)
    base = daily[(daily.index >= START) & (daily.index <= END)]
    rows = []
    for extra in (0.0, 1.0, 2.0, 5.0):
        fills = data[complete].copy()
        adjustment = np.where(fills.side.eq("buy"), fills.delta_weight * (fills.ask_price / fills.reference_mid - 1), fills.delta_weight * (1 - fills.bid_price / fills.reference_mid)) + fills.delta_weight * extra / 10000
        costs = pd.Series(np.asarray(adjustment), index=pd.to_datetime(fills.session_date)).groupby(level=0).sum()
        net = base.gross_pnl.subtract(costs, fill_value=0)
        monthly = net.groupby(net.index.to_period("M")).sum()
        rows.append({"extra_adverse_bps_per_side": extra, "net_simple_return": float(net.sum()), "maximum_drawdown": dd(net),
                     "role_coverage": float(complete.mean()), "trade_roles": int(len(data)), "trade_sessions": int(fills.session_date.nunique()),
                     "trade_session_fraction": float(fills.session_date.nunique() / len(base)), "positive_months": int((monthly > 0).sum()),
                     "negative_months": int((monthly < 0).sum()), "monthly_average": float(monthly.mean()), "monthly_median": float(monthly.median()),
                     "worst_month": float(monthly.min()), "best_month": float(monthly.max())})
        net.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT / f"daily_{extra:g}bps.parquet", index=False)
    metrics = pd.DataFrame(rows)
    metrics.to_csv(OUT / "quote_metrics.csv", index=False)
    report = {"status": "completed" if metrics.role_coverage.min() == 1 else "failed", "run_id": "RUN-0039", "metrics": metrics.to_dict("records"),
              "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0, "broker_margin": False}
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n")
    record = yaml.safe_load(RUN.read_text()); record["status"] = report["status"]; record["result"] = report
    record["decision"] = "Compare against quote-confirmed base and hard-persistence corr-capped variants on return, drawdown, cadence, and concentration."
    RUN.write_text(yaml.safe_dump(record, sort_keys=False)); print(metrics.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("phase", choices=["ledgers", "missing", "replay"]); args = parser.parse_args()
    {"ledgers": ledgers, "missing": missing, "replay": replay}[args.phase]()
