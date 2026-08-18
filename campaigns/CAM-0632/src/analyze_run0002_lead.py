from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from run0002_structural_scalps import PAIRS, build_contexts, load_bars, metrics, trades_for_variant


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0002"


def main() -> None:
    contexts, calendar, _ = build_contexts(load_bars())
    pair = next(pair for pair in PAIRS if pair[0] == "QQQ")
    trades = trades_for_variant(contexts, pair, 1, 15, 40, "impulse_reversal")
    trades["signal_et"] = trades.signal_ts.dt.tz_convert("America/New_York")
    trades["time_bucket"] = pd.cut(
        trades.signal_et.dt.hour * 60 + trades.signal_et.dt.minute,
        [0, 600, 720, 840, 940, 1440],
        labels=["pre1000", "1000_1200", "1200_1400", "1400_1540", "post1540"],
        right=False,
    )
    trades["impulse_bucket_bp"] = pd.cut(trades.signal_strength * 10_000, [40, 60, 100, 200, float("inf")], labels=["40_60", "60_100", "100_200", "200_plus"], right=False)
    rows = []
    paths = []
    for cost in [0, 1, 2, 5, 10]:
        result, daily = metrics(trades, calendar, cost)
        rows.append(result)
        for period_type, grouped in [
            ("weekly", daily.groupby(daily.index.to_period("W-FRI")).sum()),
            ("monthly", daily.groupby(daily.index.to_period("M")).sum()),
            ("yearly", daily.groupby(daily.index.to_period("Y")).sum()),
        ]:
            paths.extend({"cost_bp_side": cost, "period_type": period_type, "period": str(period), "net_return": float(value)} for period, value in grouped.items())
    attribution = []
    for dimension in ["symbol", "time_bucket", "impulse_bucket_bp"]:
        for key, group in trades.groupby(dimension, observed=True):
            attribution.append({"dimension": dimension, "key": str(key), "trades": len(group), "gross_return": group.gross_return.sum(), "net_return_5bp": (group.gross_return - 0.001).sum()})
    trades.to_csv(OUT / "lead_trade_ledger.csv", index=False)
    pd.DataFrame(rows).to_csv(OUT / "lead_cost_curve.csv", index=False)
    pd.DataFrame(paths).to_csv(OUT / "lead_weekly_monthly_yearly.csv", index=False)
    pd.DataFrame(attribution).to_csv(OUT / "lead_attribution.csv", index=False)
    report = {
        "variant": "QQQ_TQQQ_SQQQ_impulse_reversal_f1_h15_t40",
        "interpretation": "only 5bp all-block-positive row; sparse and recently negative",
        "cost_curve": rows,
        "top_absolute_trade_share": float(trades.assign(a=trades.gross_return.abs()).nlargest(10, "a").gross_return.abs().sum() / trades.gross_return.abs().sum()),
    }
    (OUT / "lead_diagnostic_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
