from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0001 import (
    RunConfig,
    _max_drawdown_and_recovery,
    simulate,
)


WINDOWS = {"18m": "2024-11-01", "15m": "2025-02-01", "12m": "2025-05-01"}


def profile(daily: pd.DataFrame, start: pd.Timestamp) -> dict:
    d = daily[daily["date"] >= start].copy()
    monthly = d.assign(month=d["date"].dt.to_period("M")).groupby("month")["net_pnl"].sum()
    max_dd, recovery, unresolved = _max_drawdown_and_recovery(d)
    return {
        "start": start.date().isoformat(),
        "end": d["date"].max().date().isoformat(),
        "net": float(d["net_pnl"].sum()),
        "avg_month": float(monthly.mean()),
        "median_month": float(monthly.median()),
        "negative_months": int((monthly < 0).sum()),
        "max_dd": max_dd,
        "recovery_days": recovery,
        "unresolved": unresolved,
        "monthly": {str(k): float(v) for k, v in monthly.items()},
    }


def benchmark(frame: pd.DataFrame, symbols: list[str], start: str) -> dict:
    opens = frame.pivot(index="date", columns="symbol", values="open").sort_index()
    opens.index = pd.to_datetime(opens.index)
    opens = opens.loc[opens.index >= pd.Timestamp(start), symbols]
    weights = {symbol: 1.0 / len(symbols) for symbol in symbols}
    daily = pd.DataFrame({"date": opens.index, "net_pnl": 0.0})
    for symbol, weight in weights.items():
        entry = float(opens.iloc[0][symbol])
        daily["net_pnl"] += weight / entry * opens[symbol].diff().fillna(0.0).to_numpy()
    daily.loc[daily.index[0], "net_pnl"] -= 0.0005
    daily.loc[daily.index[-1], "net_pnl"] -= 0.0005
    return profile(daily, pd.Timestamp(start))


def monitor_months(monthly: pd.Series, lookback: int) -> set[str]:
    periods = list(monthly.index)
    allowed = set()
    for i, period in enumerate(periods):
        if i < lookback or float(monthly.iloc[i - lookback:i].sum()) > 0:
            allowed.add(str(period))
    return allowed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    frame = pd.read_parquet(args.cache)
    frame["date"] = pd.to_datetime(frame["date"])
    config = RunConfig(
        trade_symbols=("TQQQ", "SOXL"),
        lookback=5,
        market_sma=20,
        holding_sessions=10,
        breadth=2,
        require_sma_rising=True,
        cost_bps_per_side=5.0,
    )
    daily, trades, metrics = simulate(frame, config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    daily.to_csv(args.output_dir / "candidate_daily.csv", index=False)
    trades.to_csv(args.output_dir / "candidate_trades.csv", index=False)

    monthly = daily.assign(month=daily["date"].dt.to_period("M")).groupby("month")["net_pnl"].sum()
    halfyear_rows = []
    for (year, half), group in daily.groupby(
        [daily["date"].dt.year, np.where(daily["date"].dt.month <= 6, 1, 2)]
    ):
        m = group.assign(month=group["date"].dt.to_period("M")).groupby("month")["net_pnl"].sum()
        dd, recovery, unresolved = _max_drawdown_and_recovery(group)
        halfyear_rows.append({
            "year": int(year), "half": int(half), "net": float(group["net_pnl"].sum()),
            "avg_month": float(m.mean()), "negative_months": int((m < 0).sum()),
            "max_dd": dd, "recovery_days": recovery, "unresolved": unresolved,
        })
    halfyears = pd.DataFrame(halfyear_rows)
    halfyears.to_csv(args.output_dir / "halfyears.csv", index=False)

    rolling_rows = []
    periods = list(monthly.index)
    for i in range(11, len(periods)):
        window_periods = periods[i - 11:i + 1]
        start = window_periods[0].start_time
        end = window_periods[-1].end_time
        d = daily[(daily["date"] >= start) & (daily["date"] <= end)].copy()
        dd, recovery, unresolved = _max_drawdown_and_recovery(d)
        values = monthly.loc[window_periods]
        rolling_rows.append({
            "end_month": str(window_periods[-1]),
            "net": float(values.sum()),
            "avg_month": float(values.mean()),
            "median_month": float(values.median()),
            "negative_months": int((values < 0).sum()),
            "max_dd": dd,
            "recovery_days": recovery,
            "unresolved": unresolved,
        })
    rolling = pd.DataFrame(rolling_rows)
    rolling.to_csv(args.output_dir / "rolling12.csv", index=False)
    final_rolling = rolling.iloc[-1]

    recent_trades = trades[trades["entry_date"] >= pd.Timestamp("2025-05-01")].copy()
    decisions = recent_trades.groupby("entry_date")["net_return_contribution"].sum()
    rng = np.random.default_rng(20260728)
    boot = rng.choice(decisions.to_numpy(), size=(20_000, len(decisions)), replace=True).sum(axis=1)
    observed_net = float(decisions.sum())
    positives = decisions[decisions > 0].sort_values(ascending=False)
    months_recent = 12

    monitor_results = {}
    for lookback in [3, 6]:
        allowed = monitor_months(monthly, lookback)
        mdaily, mtrades, mmetrics = simulate(frame, config, allowed_decision_months=allowed)
        monitor_results[f"trailing_{lookback}m_positive"] = {
            "allowed_months": len(allowed),
            "full": {
                "net": mmetrics["net_full_period_simple_return"],
                "max_dd": mmetrics["standard_max_drawdown"],
                "recovery_days": mmetrics["max_full_recovery_time_days"],
                "decisions": mmetrics["independent_entry_decisions"],
            },
            "recent_windows": mmetrics["recent_windows"],
        }
        mdaily.to_csv(args.output_dir / f"monitor_{lookback}m_daily.csv", index=False)
        mtrades.to_csv(args.output_dir / f"monitor_{lookback}m_trades.csv", index=False)

    unconditional_results = {}
    for name, symbols in {
        "leveraged": ("TQQQ", "SOXL"),
        "unleveraged": ("QQQ", "SMH"),
    }.items():
        unconditional_config = RunConfig(
            trade_symbols=symbols,
            lookback=5,
            market_sma=20,
            holding_sessions=10,
            breadth=2,
            require_positive_momentum=False,
            require_market_trend=False,
            cost_bps_per_side=5.0,
        )
        _, _, unconditional_metrics = simulate(frame, unconditional_config)
        unconditional_results[name] = unconditional_metrics

    diagnostics = {
        "candidate_configuration": metrics["configuration"],
        "candidate_metrics": metrics,
        "research_breadth_before_run": {
            "meaningful_executed_variants": 171,
            "note": (
                "Includes all preserved variants in RUN-0001 through RUN-0007; "
                "the selected configuration is adapted, not untouched."
            ),
        },
        "rolling_12m": {
            "window_count": int(len(rolling)),
            "final": final_rolling.to_dict(),
            "final_avg_month_percentile": float(
                (rolling["avg_month"] <= final_rolling["avg_month"]).mean()
            ),
            "windows_avg_month_ge_10pct": int((rolling["avg_month"] >= 0.10).sum()),
            "windows_avg_ge_10pct_neg_le_3_dd_lt_20pct": int(
                (
                    (rolling["avg_month"] >= 0.10)
                    & (rolling["negative_months"] <= 3)
                    & (rolling["max_dd"] < 0.20)
                ).sum()
            ),
        },
        "recent_decision_concentration": {
            "decision_count": int(len(decisions)),
            "observed_net_from_decision_legs": observed_net,
            "positive_decision_fraction": float((decisions > 0).mean()),
            "top_1_positive_share_of_net": float(positives.head(1).sum() / observed_net),
            "top_5_positive_share_of_net": float(positives.head(5).sum() / observed_net),
            "net_excluding_best_decision": float(observed_net - decisions.max()),
            "net_excluding_worst_decision": float(observed_net - decisions.min()),
            "bootstrap_seed": 20260728,
            "bootstrap_samples": 20_000,
            "bootstrap_net_95pct_interval": [
                float(np.quantile(boot, 0.025)),
                float(np.quantile(boot, 0.975)),
            ],
            "bootstrap_probability_net_le_zero": float((boot <= 0).mean()),
            "bootstrap_probability_avg_month_ge_10pct": float(
                (boot / months_recent >= 0.10).mean()
            ),
            "caveat": (
                "Resamples 19 non-overlapping entry-decision returns. It reflects "
                "event uncertainty but not the full 171-variant selection process "
                "or persistent regime uncertainty."
            ),
        },
        "benchmarks": {
            label: {
                "leveraged_equal_weight_buy_hold": benchmark(
                    frame, ["TQQQ", "SOXL"], start
                ),
                "unleveraged_equal_weight_buy_hold": benchmark(
                    frame, ["QQQ", "SMH"], start
                ),
            }
            for label, start in WINDOWS.items()
        },
        "fixed_notional_10session_unconditional_benchmarks": unconditional_results,
        "shadow_decay_monitors": monitor_results,
        "loaded_max_date": frame["date"].max().date().isoformat(),
        "holdout_rows_loaded": int((frame["date"] >= "2026-05-01").sum()),
    }
    (args.output_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2, default=str) + "\n", encoding="utf-8"
    )
    contract = {
        "executed_strategy_variants": 5,
        "candidate": "SMA20 above/rising, 5-session positive fund momentum, 10-session hold",
        "monitor_variants": ["prior trailing 3m shadow net > 0", "prior trailing 6m shadow net > 0"],
        "diagnostics": [
            "half-year chronology", "all rolling 12-month windows", "decision concentration",
            "20,000-sample decision bootstrap", "leveraged/unleveraged buy-hold benchmarks",
            "leveraged/unleveraged fixed-notional 10-session unconditional benchmarks",
        ],
        "loaded_max_date": diagnostics["loaded_max_date"],
        "holdout_rows_loaded": diagnostics["holdout_rows_loaded"],
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(contract, indent=2))


if __name__ == "__main__":
    main()
