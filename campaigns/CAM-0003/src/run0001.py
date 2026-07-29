from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0003 import max_drawdown_and_recovery, net_return, validate_cutoff


EVAL_START = pd.Timestamp("2024-11-01")
CUTOFF = pd.Timestamp("2026-04-30")


def summarize(frame: pd.DataFrame, strategy: str, cost: float) -> dict:
    active = frame["signal_positive"] if strategy == "positive_morning_long" else pd.Series(True, index=frame.index)
    pnl = pd.Series(0.0, index=frame.index)
    pnl.loc[active] = [
        net_return(a, b, cost)
        for a, b in zip(frame.loc[active, "entry_1530"], frame.loc[active, "exit_1559"])
    ]
    daily = pd.DataFrame({"date": pd.date_range(EVAL_START, CUTOFF, freq="D")})
    daily["net_pnl"] = daily["date"].map(dict(zip(frame["date"], pnl))).fillna(0.0)
    dd, recovery, unresolved = max_drawdown_and_recovery(daily)
    monthly = daily.assign(month=daily["date"].dt.to_period("M")).groupby("month")["net_pnl"].sum()
    windows = {}
    for label, start in [("18m", "2024-11-01"), ("15m", "2025-02-01"), ("12m", "2025-05-01")]:
        m = monthly[monthly.index >= pd.Period(start, "M")]
        selected_dates = frame.loc[active & frame["date"].ge(start), "date"]
        windows[label] = {
            "net": float(m.sum()), "avg_month": float(m.mean()),
            "median_month": float(m.median()),
            "negative_months": int((m < 0).sum()),
            "zero_months": int((m == 0).sum()), "trades": int(len(selected_dates)),
        }
    trade_pnl = pnl.loc[active].sort_values(ascending=False)
    positive = float(trade_pnl[trade_pnl > 0].sum())
    return {
        "strategy": strategy, "cost_bps_per_side": cost,
        "net": float(pnl.sum()), "trades": int(active.sum()),
        "positive_trade_fraction": float((pnl.loc[active] > 0).mean()),
        "max_drawdown": dd, "recovery_days": recovery, "unresolved": unresolved,
        "top1_positive_contribution_fraction": float(trade_pnl.iloc[0]/positive) if positive else None,
        "top5_positive_contribution_fraction": float(trade_pnl.head(5).sum()/positive) if positive else None,
        "windows": windows, "monthly": {str(k): float(v) for k, v in monthly.items()},
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    readiness_path = Path("campaigns/CAM-0003/artifacts/readiness/spy_daily_readiness.parquet")
    daily = pd.read_parquet(readiness_path)
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily[(daily["date"] >= EVAL_START) & (daily["date"] <= CUTOFF)].copy()
    validate_cutoff(daily)
    required = ["previous_close", "close_0959", "entry_1530", "exit_1559"]
    before = len(daily)
    daily = daily.dropna(subset=required)
    attrition = before-len(daily)
    if attrition:
        raise RuntimeError("unexpected signal field attrition")
    daily["first_half_hour_return"] = daily["close_0959"]/daily["previous_close"]-1
    daily["last_half_hour_gross_return"] = daily["exit_1559"]/daily["entry_1530"]-1
    daily["signal_positive"] = daily["first_half_hour_return"] > 0
    daily.to_parquet(a.output_dir / "daily_signals.parquet", index=False)
    variants = [
        summarize(daily, strategy, cost)
        for strategy in ["positive_morning_long", "always_long"]
        for cost in [2.0, 5.0, 10.0]
    ]
    if len(variants) != 6:
        raise RuntimeError("variant count mismatch")
    flat = []
    for v in variants:
        row = {k: val for k, val in v.items() if k not in {"windows", "monthly"}}
        for label, values in v["windows"].items():
            for key, val in values.items():
                row[f"{label}_{key}"] = val
        flat.append(row)
    pd.DataFrame(flat).to_csv(a.output_dir / "variants.csv", index=False)
    x = daily["first_half_hour_return"].to_numpy()
    y = daily["last_half_hour_gross_return"].to_numpy()
    slope, intercept = np.polyfit(x, y, 1)
    diagnostics = {
        "sessions": len(daily), "signal_positive_days": int(daily["signal_positive"].sum()),
        "signal_field_attrition": attrition,
        "gross_predictive_slope": float(slope), "gross_predictive_intercept": float(intercept),
        "gross_return_correlation": float(np.corrcoef(x, y)[0, 1]),
        "mean_last_half_hour_when_morning_positive": float(
            daily.loc[daily["signal_positive"], "last_half_hour_gross_return"].mean()
        ),
        "mean_last_half_hour_when_morning_nonpositive": float(
            daily.loc[~daily["signal_positive"], "last_half_hour_gross_return"].mean()
        ),
        "variants": variants,
    }
    (a.output_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    contract = {
        "executed_variant_count": 6, "expected_variant_count": 6,
        "readiness_sha256": hashlib.sha256(readiness_path.read_bytes()).hexdigest(),
        "signal": "prior regular-session last-minute close to 09:59 close > 0",
        "entry": "15:30 raw SIP minute open", "exit": "15:59 raw SIP minute close",
        "strategies": ["positive_morning_long", "always_long"], "costs": [2, 5, 10],
        "loaded_max_date": str(daily["date"].max().date()),
        "holdout_rows_loaded": int((daily["date"] >= "2026-05-01").sum()),
    }
    if contract["holdout_rows_loaded"]:
        raise RuntimeError("holdout contamination")
    (a.output_dir / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(pd.DataFrame(flat).to_string(index=False))


if __name__ == "__main__":
    main()
