from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from run0002_structural_scalps import PAIRS, build_contexts, load_bars, max_drawdown_and_recovery, trades_for_variant


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0005"
DEV_END = pd.Timestamp("2023-12-29")
VAL_START = pd.Timestamp("2024-01-02")
CUTOFF = pd.Timestamp("2026-04-30")
THRESHOLDS = [5, 10, 20]
HOLDS = [1, 2, 3, 5, 10]
MECHANISMS = ["impulse_continuation", "impulse_reversal"]
BUCKETS = {
    "open": ("09:35", "10:00"),
    "morning": ("10:00", "11:00"),
    "midday": ("11:00", "14:00"),
    "afternoon": ("14:00", "15:00"),
    "close": ("15:00", "15:41"),
}
COSTS = [1, 2, 5]


def summarize(trades: pd.DataFrame, calendar: pd.DatetimeIndex, cost_bp: int) -> dict:
    daily = pd.Series(0.0, index=calendar)
    if len(trades):
        net = trades.gross_return - 2 * cost_bp / 10000
        realized = pd.Series(net.to_numpy(), index=pd.to_datetime(trades.date)).groupby(level=0).sum()
        daily.loc[realized.index] = realized
        mean_trade = float(net.mean() * 10000)
    else:
        mean_trade = np.nan
    active = daily[daily != 0]
    halves = np.array_split(daily, 2)
    monthly = daily.groupby(daily.index.to_period("M")).sum()
    dd, recovery = max_drawdown_and_recovery(daily)
    return {
        "cost_bp_side": cost_bp,
        "trades": int(len(trades)),
        "trades_per_session": float(len(trades) / len(calendar)),
        "net_return": float(daily.sum()),
        "mean_net_trade_bp": mean_trade,
        "green_active_days": float((active > 0).mean()) if len(active) else np.nan,
        "green_all_days": float((daily > 0).mean()),
        "positive_month_fraction": float((monthly > 0).mean()),
        "half_returns": [float(part.sum()) for part in halves],
        "max_drawdown": dd,
        "recovery_sessions": recovery,
        "recent12_return": float(daily[daily.index >= pd.Timestamp("2025-05-01")].sum()),
        "worst_day": float(daily.min()),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    contexts, _, attrition = build_contexts(load_bars())
    calendars = {}
    for pair in PAIRS:
        name = "_".join(pair[:3])
        dates = pd.DatetimeIndex(sorted(context["date"] for context in contexts[name]))
        calendars[name] = {
            "development": dates[dates <= DEV_END],
            "validation": dates[(dates >= VAL_START) & (dates <= CUTOFF)],
        }
    rows = []
    ledgers = {}
    planned = len(PAIRS) * len(THRESHOLDS) * len(HOLDS) * len(MECHANISMS) * len(BUCKETS)
    for pair in PAIRS:
        pair_name = "_".join(pair[:3])
        for threshold in THRESHOLDS:
            for hold in HOLDS:
                for mechanism in MECHANISMS:
                    base = trades_for_variant(contexts, pair, 1, hold, threshold, mechanism)
                    if len(base):
                        base["local_hhmm"] = pd.to_datetime(base.signal_ts, utc=True).dt.tz_convert("America/New_York").dt.strftime("%H:%M")
                    for bucket, (start, end) in BUCKETS.items():
                        variant = f"{pair_name}_{mechanism}_t{threshold}_h{hold}_{bucket}"
                        selected = base[(base.local_hhmm >= start) & (base.local_hhmm < end)].copy() if len(base) else base.copy()
                        dev = selected[pd.to_datetime(selected.date) <= DEV_END]
                        ledgers[variant] = selected
                        result = summarize(dev, calendars[pair_name]["development"], 2)
                        rows.append({"variant": variant, "pair": pair_name, "mechanism": mechanism, "threshold_bp": threshold, "hold_min": hold, "time_bucket": bucket, **result})
    development = pd.DataFrame(rows)
    development["dev_gate"] = (
        (development.trades >= 250)
        & (development.mean_net_trade_bp > 0)
        & (development.green_active_days >= 0.50)
        & development.half_returns.apply(lambda values: all(value > 0 for value in values))
    )
    development.to_csv(OUT / "development_grid.csv", index=False)
    eligible = development[development.dev_gate].copy()
    eligible["worst_half"] = eligible.half_returns.apply(min) if len(eligible) else pd.Series(dtype=float)
    chosen = (eligible.sort_values(["worst_half", "mean_net_trade_bp", "trades"], ascending=False)
              .groupby(["pair", "mechanism"], as_index=False).head(1))
    validation_rows = []
    period_rows = []
    chosen_ledgers = []
    for row in chosen.itertuples():
        trades = ledgers[row.variant]
        val = trades[(pd.to_datetime(trades.date) >= VAL_START) & (pd.to_datetime(trades.date) <= CUTOFF)].copy()
        chosen_ledgers.append(val.assign(variant=row.variant))
        for cost in COSTS:
            result = summarize(val, calendars[row.pair]["validation"], cost)
            validation_rows.append({"variant": row.variant, "pair": row.pair, "mechanism": row.mechanism, "threshold_bp": row.threshold_bp, "hold_min": row.hold_min, "time_bucket": row.time_bucket, **result})
            net = val.assign(net_return=val.gross_return - 2 * cost / 10000).groupby("date").net_return.sum().reindex(calendars[row.pair]["validation"], fill_value=0.0)
            for period_type, series in [("weekly", net.groupby(net.index.to_period("W-FRI")).sum()), ("monthly", net.groupby(net.index.to_period("M")).sum()), ("yearly", net.groupby(net.index.to_period("Y")).sum())]:
                period_rows.extend({"variant": row.variant, "cost_bp_side": cost, "period_type": period_type, "period": str(period), "net_return": float(value)} for period, value in series.items())
    validation = pd.DataFrame(validation_rows)
    if len(validation):
        validation["validation_gate"] = (
            (validation.cost_bp_side == 2)
            & (validation.net_return > 0)
            & (validation.recent12_return > 0)
            & (validation.green_active_days >= 0.50)
            & validation.half_returns.apply(lambda values: all(value > 0 for value in values))
        )
    validation.to_csv(OUT / "validation_results.csv", index=False)
    pd.DataFrame(period_rows).to_csv(OUT / "validation_period_paths.csv", index=False)
    if chosen_ledgers:
        pd.concat(chosen_ledgers, ignore_index=True).to_csv(OUT / "validation_trade_ledgers.csv", index=False)
    report = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "planned_development_variants": planned,
        "executed_development_variants": int(len(development)),
        "development_survivors": int(development.dev_gate.sum()),
        "selected_without_validation": chosen.variant.tolist(),
        "executed_validation_rows": int(len(validation)),
        "two_bp_validation_survivors": int(validation.validation_gate.sum()) if len(validation) else 0,
        "selected_development": json.loads(chosen.replace({np.nan: None}).to_json(orient="records")),
        "validation": json.loads(validation.replace({np.nan: None}).to_json(orient="records")),
        "attrition": attrition,
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "decision_gate": "time_bucket_must_survive_unchanged_late_period_at_2bp_per_side",
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
