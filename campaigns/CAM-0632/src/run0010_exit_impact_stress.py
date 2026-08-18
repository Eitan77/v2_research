from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from run0002_structural_scalps import build_contexts, load_bars, max_drawdown_and_recovery


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0010"
SOURCE = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0009" / "sized_trade_ledgers.csv"
ACCOUNT = 2_000.0
PARTICIPATION = 0.05
EXECUTION = "stress_1000ms_5bp"
PENALTIES = [0, 10, 25, 50, 100]


def summarize(frame: pd.DataFrame, calendar: pd.DatetimeIndex) -> tuple[dict, pd.Series]:
    daily = frame.groupby("date").stressed_return.sum().reindex(calendar, fill_value=0.0)
    blocks = np.array_split(daily, 3)
    active = daily[daily != 0]
    monthly = daily.groupby(daily.index.to_period("M")).sum()
    dd, recovery = max_drawdown_and_recovery(daily)
    return {
        "trades": int(len(frame)),
        "unsupported_exits": int((~frame.exit_depth_supported).sum()),
        "net_return": float(daily.sum()),
        "recent12_return": float(daily[daily.index >= pd.Timestamp("2025-05-01")].sum()),
        "green_active_days": float((active > 0).mean()),
        "positive_month_fraction": float((monthly > 0).mean()),
        "block_returns": [float(part.sum()) for part in blocks],
        "max_drawdown": dd,
        "recovery_sessions": recovery,
        "worst_day": float(daily.min()),
        "top10_absolute_trade_share": float(frame.stressed_return.abs().nlargest(10).sum() / frame.stressed_return.abs().sum()),
    }, daily


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source = pd.read_csv(SOURCE, parse_dates=["date"])
    source = source[(source.account_notional == ACCOUNT) & (source.participation == PARTICIPATION) & (source.execution == EXECUTION)].copy()
    _, calendar, attrition = build_contexts(load_bars())
    rows = []
    ledgers = []
    periods = []
    for penalty in PENALTIES:
        frame = source.copy()
        extra = np.where(frame.exit_depth_supported, 0.0, penalty / 10000 * frame.allocated_dollars / ACCOUNT)
        frame["unsupported_exit_extra_penalty_bps"] = penalty
        frame["stressed_return"] = frame.account_return - extra
        result, daily = summarize(frame, calendar)
        rows.append({"unsupported_exit_extra_penalty_bps": penalty, **result})
        ledgers.append(frame[["trade_id", "variant", "date", "symbol", "unsupported_exit_extra_penalty_bps", "exit_depth_supported", "allocated_dollars", "account_return", "stressed_return"]])
        for period_type, grouped in [("weekly", daily.groupby(daily.index.to_period("W-FRI")).sum()), ("monthly", daily.groupby(daily.index.to_period("M")).sum()), ("yearly", daily.groupby(daily.index.to_period("Y")).sum())]:
            periods.extend({"unsupported_exit_extra_penalty_bps": penalty, "period_type": period_type, "period": str(period), "net_return": float(value)} for period, value in grouped.items())
    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "exit_impact_summary.csv", index=False)
    pd.concat(ledgers, ignore_index=True).to_csv(OUT / "exit_impact_ledgers.csv", index=False)
    pd.DataFrame(periods).to_csv(OUT / "period_paths.csv", index=False)
    report = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "account_notional": ACCOUNT,
        "entry_displayed_participation": PARTICIPATION,
        "base_execution": EXECUTION,
        "planned_penalty_rows": len(PENALTIES),
        "executed_penalty_rows": len(summary),
        "results": json.loads(summary.to_json(orient="records")),
        "attrition": attrition,
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "decision_gate": "report_full_and_recent_survival_without_selecting_penalty",
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
