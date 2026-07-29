from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0004 import CUTOFF, max_drawdown_and_recovery, protected_short_net_return


HORIZONS = [1, 2, 3, 4, 6]
WINDOW_STARTS = {
    "18m": pd.Timestamp("2024-11-01"),
    "15m": pd.Timestamp("2025-02-01"),
    "12m": pd.Timestamp("2025-05-01"),
}


def adjusted_bar_arrays(
    bars: pd.DataFrame, daily: pd.DataFrame
) -> dict[tuple[pd.Timestamp, str], pd.DataFrame]:
    bars = bars.copy()
    bars["date"] = pd.to_datetime(bars["date"])
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"])
    complete = bars.groupby(["date", "symbol"])["bar_start_ts"].size().eq(13)
    keys = complete[complete].reset_index()[["date", "symbol"]]
    bars = bars.merge(keys, on=["date", "symbol"], how="inner")
    last = (
        bars.sort_values("bar_start_ts")
        .groupby(["date", "symbol"], as_index=False)
        .tail(1)[["date", "symbol", "close"]]
        .rename(columns={"close": "raw_close"})
    )
    factors = last.merge(
        daily[["date", "symbol", "close"]].rename(
            columns={"close": "adjusted_close"}
        ),
        on=["date", "symbol"],
        how="inner",
    )
    factors["factor"] = factors["adjusted_close"] / factors["raw_close"]
    bars = bars.merge(
        factors[["date", "symbol", "factor"]],
        on=["date", "symbol"],
        how="inner",
    )
    for column in ["open", "high", "low", "close"]:
        bars[column] *= bars["factor"]
    result = {}
    for key, group in bars.groupby(["date", "symbol"], sort=False):
        ordered = group.sort_values("bar_start_ts").reset_index(drop=True)
        ordered["bar_index"] = np.arange(len(ordered))
        result[key] = ordered
    return result


def build_outcomes(
    panel: pd.DataFrame,
    arrays: dict[tuple[pd.Timestamp, str], pd.DataFrame],
) -> pd.DataFrame:
    records = []
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    for (date, symbol), signals in panel.groupby(["date", "symbol"], sort=False):
        bars = arrays.get((date, symbol))
        if bars is None or len(bars) != 13:
            continue
        start_to_index = {
            pd.Timestamp(value): int(index)
            for value, index in zip(bars["bar_start_ts"], bars["bar_index"])
        }
        for signal in signals.itertuples():
            trade_index = start_to_index.get(pd.Timestamp(signal.entry_ts))
            if trade_index is None or trade_index < 1:
                continue
            source_entry = float(bars.loc[trade_index - 1, "close"])
            actionable_entry = float(bars.loc[trade_index, "open"])
            for horizon in HORIZONS:
                exit_index = trade_index + horizon - 1
                if exit_index >= len(bars):
                    continue
                exit_ = float(bars.loc[exit_index, "close"])
                path_high = float(
                    bars.loc[trade_index:exit_index, "high"].max()
                )
                protected_short, stopped, _ = protected_short_net_return(
                    actionable_entry,
                    exit_,
                    path_high,
                    cost_bps_per_side=0.0,
                    stop_fraction=0.02,
                    stop_slippage_bps=5.0,
                )
                records.append(
                    {
                        "date": date,
                        "symbol": symbol,
                        "decision_ts": signal.decision_ts,
                        "horizon": horizon,
                        "residual_decile": signal.residual_decile,
                        "raw_decile": signal.raw_decile,
                        "source_long": exit_ / source_entry - 1.0,
                        "source_short": (source_entry - exit_) / source_entry,
                        "actionable_long": exit_ / actionable_entry - 1.0,
                        "actionable_short": (
                            actionable_entry - exit_
                        )
                        / actionable_entry,
                        "protected_short": protected_short,
                        "stopped": stopped,
                    }
                )
    return pd.DataFrame(records)


def make_portfolios(outcomes: pd.DataFrame) -> pd.DataFrame:
    rows = []
    executions = {
        "source_cc": ("source_long", "source_short"),
        "actionable_oc": ("actionable_long", "actionable_short"),
        "protected_oc": ("actionable_long", "protected_short"),
    }
    for model, column in [
        ("residual", "residual_decile"),
        ("raw_return", "raw_decile"),
    ]:
        for breadth, low_cut, high_cut in [
            ("decile", 1, 10),
            ("quintile", 2, 9),
        ]:
            for (date, decision_ts, horizon), group in outcomes.groupby(
                ["date", "decision_ts", "horizon"], sort=True
            ):
                low = group[group[column] <= low_cut]
                high = group[group[column] >= high_cut]
                if low.empty or high.empty:
                    continue
                for execution, (long_col, short_col) in executions.items():
                    pnl = 0.5 * float(low[long_col].mean()) + 0.5 * float(
                        high[short_col].mean()
                    )
                    rows.append(
                        {
                            "date": date,
                            "decision_ts": decision_ts,
                            "decision_period": pd.Timestamp(decision_ts)
                            .tz_convert("America/New_York")
                            .strftime("%H:%M"),
                            "variant": (
                                f"{model}_{breadth}_M{horizon}_{execution}"
                            ),
                            "model": model,
                            "breadth": breadth,
                            "horizon": horizon,
                            "execution": execution,
                            "net_pnl": pnl,
                            "low_count": len(low),
                            "high_count": len(high),
                            "stop_count": int(high["stopped"].sum())
                            if execution == "protected_oc"
                            else 0,
                        }
                    )
    return pd.DataFrame(rows)


def summarize(
    decisions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    metric_rows = []
    month_rows = []
    period_rows = []
    for variant, frame in decisions.groupby("variant"):
        daily = frame.groupby("date", as_index=False)["net_pnl"].sum()
        months = pd.period_range("2024-11", "2026-04", freq="M")
        monthly = (
            daily.assign(month=pd.to_datetime(daily["date"]).dt.to_period("M"))
            .groupby("month")["net_pnl"]
            .sum()
            .reindex(months, fill_value=0.0)
        )
        dd, recovery, unresolved = max_drawdown_and_recovery(daily)
        mean_decision = float(frame["net_pnl"].mean())
        row = {
            "variant": variant,
            "model": frame["model"].iloc[0],
            "breadth": frame["breadth"].iloc[0],
            "horizon": int(frame["horizon"].iloc[0]),
            "execution": frame["execution"].iloc[0],
            "full_gross_simple_return": float(daily["net_pnl"].sum()),
            "mean_decision_bps": mean_decision * 10_000.0,
            "break_even_cost_bps_per_side": mean_decision * 10_000.0 / 2.0,
            "standard_max_drawdown": dd,
            "max_recovery_days": recovery,
            "recovery_unresolved": unresolved,
            "decision_count": int(len(frame)),
            "trading_days": int(daily["date"].nunique()),
            "stop_count": int(frame["stop_count"].sum()),
            "average_month_18m": float(monthly.mean()),
            "negative_months_18m": int((monthly < 0).sum()),
        }
        for label, start in WINDOW_STARTS.items():
            subset = monthly[monthly.index >= start.to_period("M")]
            row[f"average_month_{label}"] = float(subset.mean())
            row[f"negative_months_{label}"] = int((subset < 0).sum())
        metric_rows.append(row)
        for month, value in monthly.items():
            month_rows.append(
                {"variant": variant, "month": str(month), "gross_pnl": value}
            )
        for period, value in frame.groupby("decision_period")["net_pnl"].mean().items():
            period_rows.append(
                {
                    "variant": variant,
                    "decision_period": period,
                    "mean_decision_bps": value * 10_000.0,
                    "decision_count": int(
                        (frame["decision_period"] == period).sum()
                    ),
                }
            )
    metrics = pd.DataFrame(metric_rows).sort_values(
        ["average_month_15m", "standard_max_drawdown"],
        ascending=[False, True],
    )
    return metrics, pd.DataFrame(month_rows), pd.DataFrame(period_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--readiness-dir", type=Path, required=True)
    parser.add_argument("--parent-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    panel = pd.read_parquet(args.parent_dir / "decision_panel.parquet")
    bars = pd.read_parquet(args.readiness_dir / "regular_30m_bars.parquet")
    daily = pd.read_parquet(args.readiness_dir / "daily_split_adjusted.parquet")
    arrays = adjusted_bar_arrays(bars, daily)
    outcomes = build_outcomes(panel, arrays)
    decisions = make_portfolios(outcomes)
    variants, monthly, by_period = summarize(decisions)
    if len(variants) != 60:
        raise RuntimeError(f"expected 60 variants, executed {len(variants)}")
    if outcomes["date"].max() > CUTOFF or int(
        (outcomes["date"] >= "2026-05-01").sum()
    ):
        raise RuntimeError("holdout validation failed")
    outcomes.to_parquet(args.output_dir / "outcomes.parquet", index=False)
    decisions.to_parquet(
        args.output_dir / "portfolio_decisions.parquet", index=False
    )
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    by_period.to_csv(args.output_dir / "by_period.csv", index=False)
    diagnostics = {
        "status": "passed",
        "max_loaded_date": str(outcomes["date"].max().date()),
        "holdout_rows_loaded": 0,
        "outcome_rows": int(len(outcomes)),
        "decision_rows": int(len(decisions)),
        "variant_count": int(len(variants)),
        "unprotected_variants_are_diagnostic_only": True,
        "protected_variants_are_bar_screens_not_execution_qualified": True,
    }
    contract = {
        "command": (
            "python campaigns/CAM-0004/src/run0003.py "
            "--readiness-dir campaigns/CAM-0004/artifacts/readiness "
            "--parent-dir campaigns/CAM-0004/artifacts/RUN-0002 "
            "--output-dir campaigns/CAM-0004/artifacts/RUN-0003"
        ),
        "resolved_defaults": {
            "models": ["residual", "raw_return"],
            "breadth": ["decile", "quintile"],
            "holding_periods": HORIZONS,
            "executions": ["source_cc", "actionable_oc", "protected_oc"],
            "cost_bps_per_side": 0,
        },
        "executed_variant_count": int(len(variants)),
        "output_paths": [
            "outcomes.parquet",
            "portfolio_decisions.parquet",
            "variants.csv",
            "monthly.csv",
            "by_period.csv",
            "contract.json",
            "diagnostics.json",
        ],
    }
    (args.output_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(variants.head(20).to_string(index=False))
    print(json.dumps(diagnostics, indent=2))


if __name__ == "__main__":
    main()
