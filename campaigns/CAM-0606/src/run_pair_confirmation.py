from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
CAM = ROOT / "campaigns"
OUT = CAM / "CAM-0606" / "artifacts" / "RUN-0024"
PARENT = CAM / "CAM-0606" / "artifacts" / "RUN-0023"


def max_dd(pnl):
    equity = 1 + pnl.cumsum()
    return float(((equity.cummax() - equity) / equity.cummax()).max())


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    fills = pd.read_parquet(PARENT / "quote_fills.parquet")
    entries = fills[["clock", "session_date", "symbol", "leg", "entry_bid", "entry_ask"]].copy()
    entries["entry_mid"] = (entries.entry_bid + entries.entry_ask) / 2
    pivot = entries.pivot(index=["session_date", "symbol", "leg"], columns="clock", values="entry_mid").reset_index()
    pivot["first10_return"] = pivot["0940"] / pivot["0930"] - 1
    relative = pivot.pivot(index="session_date", columns="leg", values="first10_return")
    confirmed_dates = relative.index[relative["short"] < relative["long"]]
    daily = pd.read_parquet(PARENT / "quote_daily.parquet")
    daily["date"] = pd.to_datetime(daily.date)
    daily = daily[daily.clock.eq("0940") & daily.date.isin(confirmed_dates)].copy()
    rows = []
    for extra, group in daily.groupby("extra_slippage_bps_per_side"):
        net = group.set_index("date").net_pnl.sort_index()
        monthly = net.groupby(net.index.to_period("M")).sum().reindex(pd.period_range("2025-05", "2026-04", freq="M"), fill_value=0.0)
        rows.append({"extra_slippage_bps_per_side": float(extra), "net_simple_return": float(net.sum()), "maximum_drawdown": max_dd(net), "trades": int(len(net)), "green_trades": int((net > 0).sum()), "red_trades": int((net < 0).sum()), "win_rate": float((net > 0).mean()), "positive_months": int((monthly > 0).sum()), "negative_months": int((monthly < 0).sum()), "inactive_months": int((monthly == 0).sum()), "worst_month": float(monthly.min()), "best_month": float(monthly.max())})
    metrics = pd.DataFrame(rows)
    report = {"status": "completed", "run_id": "RUN-0024", "parent_events": int(relative.shape[0]), "confirmed_events": int(len(confirmed_dates)), "retention": float(len(confirmed_dates)/len(relative)), "metrics": metrics.to_dict("records"), "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0, "interpretation": "Development-only causal first-ten-minute convergence confirmation; no threshold search."}
    metrics.to_csv(OUT / "confirmation_metrics.csv", index=False)
    relative.assign(confirmed=relative.index.isin(confirmed_dates)).to_csv(OUT / "event_confirmation.csv")
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    path = CAM / "CAM-0606" / "runs" / "RUN-0024.yaml"; run = yaml.safe_load(path.read_text(encoding="utf-8")); run["status"] = "completed"; run["result"] = report; run["decision"] = "Advance only if event count and 2-5 bp robustness improve materially; otherwise reject confirmation and retain non-promotion."; path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    with (CAM / "CAM-0606" / "WORKLOG.jsonl").open("a", encoding="utf-8") as handle: handle.write(json.dumps({"run_id": "RUN-0024", "event": "completed", "result": report}) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__": main()
