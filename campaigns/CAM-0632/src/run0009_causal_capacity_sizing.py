from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from run0002_structural_scalps import build_contexts, load_bars, max_drawdown_and_recovery


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0009"
SOURCE = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0006" / "trade_audit.csv"
VARIANTS = [
    "QQQ_TQQQ_SQQQ_reversal_t40_h15_one_bar_opposite_ov0",
    "SMH_SOXL_SOXS_reversal_t50_h20_none_ov20",
]
ACCOUNTS = [2_000, 10_000, 25_000, 50_000]
PARTICIPATION = [0.05, 0.10, 0.25]
ROUND_LOT_SHARES = 100.0
TARGET_SLEEVE = 0.50


def metrics(frame: pd.DataFrame, calendar: pd.DatetimeIndex) -> tuple[dict, pd.Series]:
    daily = frame.groupby("date").account_return.sum().reindex(calendar, fill_value=0.0)
    active = daily[daily != 0]
    blocks = np.array_split(daily, 3)
    monthly = daily.groupby(daily.index.to_period("M")).sum()
    dd, recovery = max_drawdown_and_recovery(daily)
    return {
        "trades": int(len(frame)),
        "net_return": float(daily.sum()),
        "recent12_return": float(daily[daily.index >= pd.Timestamp("2025-05-01")].sum()),
        "mean_target_sleeve_utilization": float(frame.sleeve_utilization.mean()),
        "median_target_sleeve_utilization": float(frame.sleeve_utilization.median()),
        "full_target_fraction": float(frame.full_target.mean()),
        "later_exit_depth_support_fraction": float(frame.exit_depth_supported.mean()),
        "green_active_days": float((active > 0).mean()),
        "positive_month_fraction": float((monthly > 0).mean()),
        "block_returns": [float(part.sum()) for part in blocks],
        "max_drawdown": dd,
        "recovery_sessions": recovery,
        "worst_day": float(daily.min()),
        "top10_absolute_trade_share": float(frame.account_return.abs().nlargest(10).sum() / frame.account_return.abs().sum()),
    }, daily


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source = pd.read_csv(SOURCE, parse_dates=["date"])
    source = source[source.variant.isin(VARIANTS)].copy()
    _, calendar, attrition = build_contexts(load_bars())
    rows = []
    ledgers = []
    paths = []
    for account in ACCOUNTS:
        for participation in PARTICIPATION:
            sized = source.copy()
            target_dollars = account * TARGET_SLEEVE
            max_depth_shares = sized.entry_ask_size_lots * ROUND_LOT_SHARES * participation
            target_shares = target_dollars / sized.entry_ask
            sized["quantity"] = np.floor(np.minimum(target_shares, max_depth_shares))
            if (sized.quantity < 1).any():
                raise RuntimeError("capacity rule produced zero-share trade")
            sized["allocated_dollars"] = sized.quantity * sized.entry_ask
            sized["sleeve_utilization"] = sized.allocated_dollars / target_dollars
            sized["full_target"] = sized.quantity >= np.floor(target_shares)
            sized["exit_depth_supported"] = sized.quantity <= sized.exit_bid_size_lots * ROUND_LOT_SHARES * participation
            for execution, return_column in [("primary_250ms_2bp", "net_return"), ("stress_1000ms_5bp", "stress_net_return")]:
                frame = sized.copy()
                frame["account_return"] = frame[return_column] * frame.allocated_dollars / account
                frame["account_notional"] = account
                frame["participation"] = participation
                frame["execution"] = execution
                result, daily = metrics(frame, calendar)
                rows.append({"account_notional": account, "participation": participation, "execution": execution, **result})
                ledgers.append(frame[["trade_id", "variant", "date", "symbol", "account_notional", "participation", "execution", "quantity", "allocated_dollars", "sleeve_utilization", "exit_depth_supported", "account_return"]])
                for period_type, grouped in [("weekly", daily.groupby(daily.index.to_period("W-FRI")).sum()), ("monthly", daily.groupby(daily.index.to_period("M")).sum()), ("yearly", daily.groupby(daily.index.to_period("Y")).sum())]:
                    paths.extend({"account_notional": account, "participation": participation, "execution": execution, "period_type": period_type, "period": str(period), "net_return": float(value)} for period, value in grouped.items())
    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "sizing_summary.csv", index=False)
    pd.concat(ledgers, ignore_index=True).to_csv(OUT / "sized_trade_ledgers.csv", index=False)
    pd.DataFrame(paths).to_csv(OUT / "period_paths.csv", index=False)
    default = summary[(summary.participation == 0.05) & (summary.execution == "stress_1000ms_5bp")]
    report = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "planned_rows": len(ACCOUNTS) * len(PARTICIPATION) * 2,
        "executed_rows": len(summary),
        "default_conservative_stress": json.loads(default.to_json(orient="records")),
        "all_rows": json.loads(summary.to_json(orient="records")),
        "attrition": attrition,
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "decision_gate": "entry_depth_capped_stress_blocks_positive_and_utilization_reported",
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
