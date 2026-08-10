from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "campaigns" / "CAM-0600" / "src"
sys.path.insert(0, str(SRC))

from replay_notable_neighborhoods import END, OUT as REPLAY_OUT, START, quote_cache, selected
from suite_core import CAMPAIGNS

OUT = CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0037"
RUN = CAMPAIGNS / "CAM-0600" / "runs" / "RUN-0037.yaml"


def quote_adjustments():
    ledger = pd.read_parquet(REPLAY_OUT / "ledger_0940.parquet")
    ledger.target_ts = pd.to_datetime(ledger.target_ts, utc=True)
    quotes = quote_cache("0940")
    data = ledger.merge(quotes[["symbol", "target_ts", "role", "bid_price", "ask_price"]], on=["symbol", "target_ts", "role"], validate="many_to_one")
    reference_ledger = pd.read_parquet(REPLAY_OUT / "ledger_0930.parquet")
    reference_ledger.target_ts = pd.to_datetime(reference_ledger.target_ts, utc=True)
    reference_quotes = quote_cache("0930")
    reference = reference_ledger.merge(reference_quotes[["symbol", "target_ts", "role", "bid_price", "ask_price"]], on=["symbol", "target_ts", "role"], validate="many_to_one")
    reference["reference_mid"] = (reference.bid_price + reference.ask_price) / 2
    data = data.merge(reference[["candidate", "session_date", "symbol", "side", "reference_mid"]], on=["candidate", "session_date", "symbol", "side"], validate="one_to_one")
    data["adjustment"] = np.where(
        data.side.eq("buy"),
        data.delta_weight * (data.ask_price / data.reference_mid - 1),
        data.delta_weight * (1 - data.bid_price / data.reference_mid),
    ) + data.delta_weight * 2 / 10000
    return data


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    adjustments = quote_adjustments()
    selected_weights = selected()
    concentration = []
    for name, (panel, weights) in selected_weights.items():
        executed = np.zeros_like(weights)
        executed[1:] = weights[:-1]
        returns = panel.open_to_next_open_return.copy()
        returns[-1] = panel.open_to_close_return[-1]
        gross = executed * np.nan_to_num(returns, nan=0.0)
        dates = pd.to_datetime(panel.dates)
        mask = (dates >= START) & (dates <= END)
        symbol = pd.Series(gross[mask].sum(axis=0), index=panel.symbols.astype(str))
        costs = adjustments[adjustments.candidate == name].groupby("symbol").adjustment.sum()
        net = symbol.subtract(costs, fill_value=0).sort_values(ascending=False)
        positive = net.clip(lower=0)
        total_positive = float(positive.sum())
        daily = pd.read_parquet(REPLAY_OUT / f"daily_{name}_2bps.parquet")
        daily.date = pd.to_datetime(daily.date)
        series = daily.set_index("date").net_pnl
        monthly = series.groupby(series.index.to_period("M")).sum()
        record = {
            "candidate": name,
            "net_simple_return": float(series.sum()),
            "top_symbol": str(net.index[0]),
            "top_symbol_net": float(net.iloc[0]),
            "top_symbol_positive_share": float(positive.iloc[0] / total_positive),
            "top5_symbol_positive_share": float(positive.head(5).sum() / total_positive),
            "leave_top1_out_return": float(series.sum() - net.iloc[0]),
            "leave_top5_out_return": float(series.sum() - net.head(5).sum()),
            "sndk_net": float(net.get("SNDK", 0.0)),
            "first6_return": float(monthly.iloc[:6].sum()),
            "last6_return": float(monthly.iloc[6:].sum()),
            "positive_months": int((monthly > 0).sum()),
            "worst_month": float(monthly.min()),
            "trade_session_fraction": float(adjustments[adjustments.candidate == name].session_date.nunique() / len(series)),
            "preferred_cadence_pass": bool(adjustments[adjustments.candidate == name].session_date.nunique() / len(series) >= 0.5),
        }
        concentration.append(record)
        net.rename("net_pnl").rename_axis("symbol").reset_index().to_csv(OUT / f"symbols_{name}.csv", index=False)
        monthly.rename("net_pnl").rename_axis("month").reset_index().to_csv(OUT / f"monthly_{name}.csv", index=False)

    daily_series = {}
    for name in selected_weights:
        data = pd.read_parquet(REPLAY_OUT / f"daily_{name}_2bps.parquet")
        data.date = pd.to_datetime(data.date)
        daily_series[name] = data.set_index("date").net_pnl
    prior = CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0034"
    for name, label in (("ma200_top10_p3", "ma200_uncapped"), ("ma50_200_top10_p3", "ma50_200")):
        data = pd.read_parquet(prior / f"daily_{label}_0940_2bps.parquet")
        data.date = pd.to_datetime(data.date)
        daily_series[name] = data.set_index("date").net_pnl
    correlation = pd.DataFrame(daily_series).corr()
    correlation.to_csv(OUT / "daily_pnl_correlation.csv")
    frame = pd.DataFrame(concentration)
    frame.to_csv(OUT / "concentration_summary.csv", index=False)
    report = {
        "status": "completed",
        "run_id": "RUN-0037",
        "concentration": concentration,
        "key_correlations": {
            "native_vs_strict_history_ma": float(correlation.loc["ma200_top5_native_p5", "ma200_top5_history252_p5"]),
            "native_ma_vs_dual_ma_top5": float(correlation.loc["ma200_top5_native_p5", "ma50_200_top5_p5"]),
            "ma_top10_p3_vs_dual_top10_p3": float(correlation.loc["ma200_top10_p3", "ma50_200_top10_p3"]),
            "native_ma_top5_p5_vs_triple": float(correlation.loc["ma200_top5_native_p5", "triple_ma10_50_200_top3"]),
        },
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "broker_margin": False,
    }
    report = json.loads(json.dumps(report, default=lambda x: x.item() if isinstance(x, np.generic) else str(x)))
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n")
    record = yaml.safe_load(RUN.read_text())
    record["status"] = "completed"
    record["result"] = report
    record["decision"] = "Prefer cadence-compliant, less-concentrated variants; do not count highly correlated MA variants as independent discoveries."
    RUN.write_text(yaml.safe_dump(record, sort_keys=False))
    print(frame.to_string(index=False))
    print(json.dumps(report["key_correlations"], indent=2))


if __name__ == "__main__":
    main()
