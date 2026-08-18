from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from run0002_structural_scalps import build_contexts, load_bars, max_drawdown_and_recovery


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0006"
SOURCE = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0003" / "top_trade_ledgers.csv"
QUOTES = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0004" / "quote_ledgers.csv"
VARIANTS = [
    "SMH_SOXL_SOXS_reversal_t60_h15_none_ov0",
    "SMH_SOXL_SOXS_reversal_t50_h20_none_ov20",
    "QQQ_TQQQ_SQQQ_reversal_t40_h15_one_bar_opposite_ov0",
]
PRIMARY = (250, 2)
STRESS = (1000, 5)


def period_metrics(frame: pd.DataFrame, calendar: pd.DatetimeIndex) -> tuple[dict, pd.Series]:
    daily = frame.groupby("date").net_return.sum().reindex(calendar, fill_value=0.0)
    blocks = np.array_split(daily, 3)
    active = daily[daily != 0]
    monthly = daily.groupby(daily.index.to_period("M")).sum()
    dd, recovery = max_drawdown_and_recovery(daily)
    return {
        "trades": int(len(frame)),
        "net_return": float(daily.sum()),
        "recent12_return": float(daily[daily.index >= pd.Timestamp("2025-05-01")].sum()),
        "mean_net_trade_bps": float(frame.net_return.mean() * 10000),
        "green_active_days": float((active > 0).mean()),
        "positive_month_fraction": float((monthly > 0).mean()),
        "block_returns": [float(part.sum()) for part in blocks],
        "max_drawdown": dd,
        "recovery_sessions": recovery,
        "worst_day": float(daily.min()),
        "best_day": float(daily.max()),
    }, daily


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source = pd.read_csv(SOURCE, parse_dates=["date", "signal_ts", "entry_ts", "exit_ts"])
    source = source[source.variant.isin(VARIANTS)].drop_duplicates(["variant", "date", "symbol", "entry_ts", "exit_ts"]).reset_index(drop=True)
    source.index.name = "trade_id"
    quote = pd.read_csv(QUOTES, parse_dates=["date", "entry_quote_ts", "exit_quote_ts"])
    quote["entry_quote_ts"] = pd.to_datetime(quote.entry_quote_ts, utc=True, format="mixed")
    quote["exit_quote_ts"] = pd.to_datetime(quote.exit_quote_ts, utc=True, format="mixed")
    primary = quote[(quote.latency_ms == PRIMARY[0]) & (quote.extra_bp_side == PRIMARY[1])].copy()
    stress = quote[(quote.latency_ms == STRESS[0]) & (quote.extra_bp_side == STRESS[1])][["trade_id", "net_return"]].rename(columns={"net_return": "stress_net_return"})
    if len(primary) != len(source) or primary.trade_id.nunique() != len(source):
        raise RuntimeError("quote/source trade count mismatch")
    keys = source.reset_index()[["trade_id", "variant", "date", "symbol", "entry_ts", "exit_ts", "gross_return"]]
    audit = primary.merge(keys, on="trade_id", suffixes=("_quote", "_source"), validate="one_to_one").merge(stress, on="trade_id", validate="one_to_one")
    key_failures = int(((audit.variant_quote != audit.variant_source) | (audit.symbol_quote != audit.symbol_source) | (pd.to_datetime(audit.date_quote) != pd.to_datetime(audit.date_source))).sum())
    audit = audit.rename(columns={"variant_source": "variant", "symbol_source": "symbol", "date_source": "date"})

    bars = load_bars()
    bar_open = bars.set_index(["symbol", "ts"])["open"]
    bar_close = bars.set_index(["symbol", "ts"])["close"]
    audit["bar_entry_open"] = [float(bar_open.loc[(symbol, ts)]) for symbol, ts in zip(audit.symbol, audit.entry_ts)]
    audit["bar_exit_close"] = [float(bar_close.loc[(symbol, ts - pd.Timedelta(minutes=1))]) for symbol, ts in zip(audit.symbol, audit.exit_ts)]
    audit["entry_quote_minus_bar_bps"] = (audit.entry_ask / audit.bar_entry_open - 1) * 10000
    audit["exit_quote_minus_bar_bps"] = (audit.exit_bid / audit.bar_exit_close - 1) * 10000
    audit["quote_gross_return"] = audit.exit_bid / audit.entry_ask - 1
    audit["quote_minus_bar_return_bps"] = (audit.quote_gross_return - audit.gross_return) * 10000
    audit["entry_delay_ms"] = (audit.entry_quote_ts - audit.entry_ts).dt.total_seconds() * 1000
    audit["exit_delay_ms"] = (audit.exit_quote_ts - audit.exit_ts).dt.total_seconds() * 1000
    audit["valid_nbbo"] = (audit.bid_entry > 0) & (audit.entry_ask > 0) & (audit.bid_entry <= audit.entry_ask) & (audit.exit_bid > 0) & (audit.ask_exit > 0) & (audit.exit_bid <= audit.ask_exit)
    audit["valid_size"] = (audit.entry_ask_size_lots >= 1) & (audit.exit_bid_size_lots >= 1)
    audit["valid_latency"] = audit.entry_delay_ms.between(250, 30000) & audit.exit_delay_ms.between(250, 30000)
    scale_failures = int(((audit.entry_ask / audit.bar_entry_open < 0.5) | (audit.entry_ask / audit.bar_entry_open > 2) | (audit.exit_bid / audit.bar_exit_close < 0.5) | (audit.exit_bid / audit.bar_exit_close > 2)).sum())
    audit.to_csv(OUT / "trade_audit.csv", index=False)

    contexts, _, attrition = build_contexts(bars)
    calendars = {}
    for variant in VARIANTS:
        pair = "SMH_SOXL_SOXS" if variant.startswith("SMH") else "QQQ_TQQQ_SQQQ"
        calendars[variant] = pd.DatetimeIndex(sorted(context["date"] for context in contexts[pair]))
    rows = []
    period_rows = []
    symbol_rows = []
    for variant in VARIANTS:
        frame = audit[audit.variant == variant].copy()
        metrics, daily = period_metrics(frame, calendars[variant])
        stress_frame = frame.assign(net_return=frame.stress_net_return)
        stress_metrics, stress_daily = period_metrics(stress_frame, calendars[variant])
        absolute = frame.net_return.abs().sort_values(ascending=False)
        top10_abs_share = float(absolute.head(10).sum() / absolute.sum())
        best_date = daily.idxmax()
        leave_best_date_return = float(daily.sum() - daily.max())
        covid = frame[~frame.date.between(pd.Timestamp("2020-02-20"), pd.Timestamp("2020-04-30"))]
        april25 = frame[~frame.date.between(pd.Timestamp("2025-04-01"), pd.Timestamp("2025-04-30"))]
        joint = frame[~frame.date.between(pd.Timestamp("2020-02-20"), pd.Timestamp("2020-04-30")) & ~frame.date.between(pd.Timestamp("2025-04-01"), pd.Timestamp("2025-04-30"))]
        row = {
            "variant": variant,
            **metrics,
            "stress_net_return": stress_metrics["net_return"],
            "stress_recent12_return": stress_metrics["recent12_return"],
            "stress_block_returns": stress_metrics["block_returns"],
            "top10_absolute_return_share": top10_abs_share,
            "best_date": str(best_date.date()),
            "leave_best_date_return": leave_best_date_return,
            "excluding_covid_return": float(covid.net_return.sum()),
            "excluding_april2025_return": float(april25.net_return.sum()),
            "excluding_both_return": float(joint.net_return.sum()),
            "entry_bar_diff_median_bps": float(frame.entry_quote_minus_bar_bps.median()),
            "entry_bar_diff_max_abs_bps": float(frame.entry_quote_minus_bar_bps.abs().max()),
            "exit_bar_diff_median_bps": float(frame.exit_quote_minus_bar_bps.median()),
            "exit_bar_diff_max_abs_bps": float(frame.exit_quote_minus_bar_bps.abs().max()),
            "quote_bar_return_diff_median_bps": float(frame.quote_minus_bar_return_bps.median()),
            "quote_bar_return_diff_max_abs_bps": float(frame.quote_minus_bar_return_bps.abs().max()),
            "entry_delay_max_ms": float(frame.entry_delay_ms.max()),
            "exit_delay_max_ms": float(frame.exit_delay_ms.max()),
            "minimum_entry_size_units": float(frame.entry_ask_size_lots.min()),
            "minimum_exit_size_units": float(frame.exit_bid_size_lots.min()),
        }
        row["audit_gate"] = bool(
            row["net_return"] > 0
            and row["leave_best_date_return"] > 0
            and row["excluding_both_return"] > 0
            and row["top10_absolute_return_share"] <= 0.50
            and row["stress_recent12_return"] > 0
            and all(value > 0 for value in row["stress_block_returns"])
        )
        rows.append(row)
        for cost_name, series in [("primary", daily), ("stress", stress_daily)]:
            for period_type, grouped in [("weekly", series.groupby(series.index.to_period("W-FRI")).sum()), ("monthly", series.groupby(series.index.to_period("M")).sum()), ("yearly", series.groupby(series.index.to_period("Y")).sum())]:
                period_rows.extend({"variant": variant, "execution": cost_name, "period_type": period_type, "period": str(period), "net_return": float(value)} for period, value in grouped.items())
        for symbol, group in frame.groupby("symbol"):
            symbol_rows.append({"variant": variant, "symbol": symbol, "trades": len(group), "net_return": float(group.net_return.sum()), "mean_net_trade_bps": float(group.net_return.mean() * 10000)})
    summary = pd.DataFrame(rows)
    integrity = {
        "key_failures": key_failures,
        "scale_failures_outside_half_to_double": scale_failures,
        "invalid_nbbo_rows": int((~audit.valid_nbbo).sum()),
        "invalid_size_rows": int((~audit.valid_size).sum()),
        "invalid_latency_rows": int((~audit.valid_latency).sum()),
    }
    if any(integrity.values()):
        raise RuntimeError(f"integrity failure: {integrity}")
    summary.to_csv(OUT / "variant_audit.csv", index=False)
    pd.DataFrame(period_rows).to_csv(OUT / "period_paths.csv", index=False)
    pd.DataFrame(symbol_rows).to_csv(OUT / "symbol_attribution.csv", index=False)
    report = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_trades": len(source),
        "audited_trades": len(audit),
        "integrity": integrity,
        "primary_execution": {"latency_ms": PRIMARY[0], "extra_bps_per_side": PRIMARY[1]},
        "stress_execution": {"latency_ms": STRESS[0], "extra_bps_per_side": STRESS[1]},
        "variant_audit": json.loads(summary.replace({np.nan: None}).to_json(orient="records")),
        "attrition": attrition,
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "decision_gate": "audit_pass_earns_adapted_candidate_freeze_not_consistency_claim",
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
