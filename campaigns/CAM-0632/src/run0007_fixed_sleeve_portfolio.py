from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from run0002_structural_scalps import build_contexts, load_bars, max_drawdown_and_recovery


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0007"
SOURCE = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0006" / "trade_audit.csv"
QQQ = "QQQ_TQQQ_SQQQ_reversal_t40_h15_one_bar_opposite_ov0"
SMH = "SMH_SOXL_SOXS_reversal_t50_h20_none_ov20"
VARIANTS = [QQQ, SMH]
WEIGHT = 0.5


def metrics(daily: pd.Series, trades: pd.DataFrame) -> dict:
    active = daily[daily != 0]
    monthly = daily.groupby(daily.index.to_period("M")).sum()
    weekly = daily.groupby(daily.index.to_period("W-FRI")).sum()
    blocks = np.array_split(daily, 3)
    dd, recovery = max_drawdown_and_recovery(daily)
    return {
        "trades": int(len(trades)),
        "trades_per_session": float(len(trades) / len(daily)),
        "active_day_fraction": float((daily != 0).mean()),
        "net_return": float(daily.sum()),
        "recent12_return": float(daily[daily.index >= pd.Timestamp("2025-05-01")].sum()),
        "green_active_days": float((active > 0).mean()),
        "positive_week_fraction": float((weekly > 0).mean()),
        "positive_month_fraction": float((monthly > 0).mean()),
        "block_returns": [float(part.sum()) for part in blocks],
        "max_drawdown": dd,
        "recovery_sessions": recovery,
        "worst_day": float(daily.min()),
        "best_day": float(daily.max()),
        "top10_absolute_trade_share": float(trades.weighted_return.abs().nlargest(10).sum() / trades.weighted_return.abs().sum()),
    }


def maximum_exposure(frame: pd.DataFrame) -> tuple[float, int]:
    events = []
    for row in frame.itertuples():
        events.append((row.entry_ts, 1, WEIGHT))
        events.append((row.exit_ts, 0, -WEIGHT))
    exposure = 0.0
    maximum = 0.0
    overlap_entries = 0
    for _, kind, change in sorted(events, key=lambda value: (value[0], value[1])):
        if kind == 1 and exposure > 0:
            overlap_entries += 1
        exposure += change
        maximum = max(maximum, exposure)
        if exposure < -1e-12:
            raise RuntimeError("negative exposure state")
    return maximum, overlap_entries


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    trades = pd.read_csv(SOURCE, parse_dates=["date", "entry_ts", "exit_ts"])
    trades = trades[trades.variant.isin(VARIANTS)].copy()
    if set(trades.variant) != set(VARIANTS):
        raise RuntimeError("frozen candidate missing")
    contexts, calendar, attrition = build_contexts(load_bars())
    maximum, overlap_entries = maximum_exposure(trades)
    if maximum > 1.0 + 1e-12:
        raise RuntimeError("cash-only exposure exceeded")
    rows = []
    period_rows = []
    family_rows = []
    paths = {}
    for execution, column in [("primary_250ms_2bp", "net_return"), ("stress_1000ms_5bp", "stress_net_return")]:
        frame = trades.assign(weighted_return=trades[column] * WEIGHT)
        daily = frame.groupby("date").weighted_return.sum().reindex(calendar, fill_value=0.0)
        paths[execution] = daily
        result = metrics(daily, frame)
        rows.append({"portfolio": "fixed_50_50", "execution": execution, **result})
        for period_type, grouped in [("weekly", daily.groupby(daily.index.to_period("W-FRI")).sum()), ("monthly", daily.groupby(daily.index.to_period("M")).sum()), ("yearly", daily.groupby(daily.index.to_period("Y")).sum())]:
            period_rows.extend({"portfolio": "fixed_50_50", "execution": execution, "period_type": period_type, "period": str(period), "net_return": float(value)} for period, value in grouped.items())
        for variant, group in frame.groupby("variant"):
            family_rows.append({"execution": execution, "variant": variant, "trades": len(group), "weighted_return": float(group.weighted_return.sum()), "mean_unweighted_trade_bps": float(group[column].mean() * 10000)})
    daily_family = trades.pivot_table(index="date", columns="variant", values="net_return", aggfunc="sum").reindex(calendar, fill_value=0.0)
    active_union = (daily_family[QQQ] != 0) | (daily_family[SMH] != 0)
    active_correlation = float(daily_family.loc[active_union, QQQ].corr(daily_family.loc[active_union, SMH]))
    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "portfolio_summary.csv", index=False)
    pd.DataFrame(period_rows).to_csv(OUT / "period_paths.csv", index=False)
    pd.DataFrame(family_rows).to_csv(OUT / "family_attribution.csv", index=False)
    pd.DataFrame({"date": calendar, **{name: series.to_numpy() for name, series in paths.items()}}).to_csv(OUT / "daily_paths.csv", index=False)
    report = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidate_trades": int(len(trades)),
        "sleeve_weight_each": WEIGHT,
        "maximum_concurrent_exposure": maximum,
        "overlap_entries": overlap_entries,
        "active_day_family_correlation_primary": active_correlation,
        "portfolio": json.loads(summary.replace({np.nan: None}).to_json(orient="records")),
        "family_attribution": json.loads(pd.DataFrame(family_rows).replace({np.nan: None}).to_json(orient="records")),
        "attrition": attrition,
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "decision_gate": "freeze_exact_forward_paper_spec_if_stress_positive_without_claiming_daily_consistency",
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
