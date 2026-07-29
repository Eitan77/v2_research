from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0004 import (
    CUTOFF,
    FEATURES,
    assign_tail_portfolios,
    long_net_return,
    max_drawdown_and_recovery,
    paper_rank_normalize,
    protected_short_net_return,
    source_style_residual,
    validate_cutoff,
)


EVAL_START = pd.Timestamp("2024-11-01")
WINDOW_STARTS = {
    "18m": pd.Timestamp("2024-11-01"),
    "15m": pd.Timestamp("2025-02-01"),
    "12m": pd.Timestamp("2025-05-01"),
}


def build_decision_panel(
    bars: pd.DataFrame, daily: pd.DataFrame, features: pd.DataFrame
) -> tuple[pd.DataFrame, dict]:
    bars = bars.copy()
    bars["date"] = pd.to_datetime(bars["date"])
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"])
    features = features.copy()
    features["date"] = pd.to_datetime(features["date"])
    complete = (
        bars.groupby(["date", "symbol"])["bar_start_ts"].size().eq(13)
    )
    complete_keys = complete[complete].reset_index()[["date", "symbol"]]
    bars = bars.merge(
        complete_keys, on=["date", "symbol"], how="inner", validate="many_to_one"
    )
    last_close = (
        bars.sort_values("bar_start_ts")
        .groupby(["date", "symbol"], as_index=False)
        .tail(1)[["date", "symbol", "close"]]
        .rename(columns={"close": "raw_session_close"})
    )
    adjusted = daily[["date", "symbol", "close"]].rename(
        columns={"close": "adjusted_daily_close"}
    )
    factors = last_close.merge(
        adjusted, on=["date", "symbol"], how="inner", validate="one_to_one"
    )
    factors["adjustment_factor"] = (
        factors["adjusted_daily_close"] / factors["raw_session_close"]
    )
    bars = bars.merge(
        factors[["date", "symbol", "adjustment_factor"]],
        on=["date", "symbol"],
        how="inner",
        validate="many_to_one",
    )
    for column in ["open", "high", "low", "close", "vwap"]:
        bars[column] = bars[column] * bars["adjustment_factor"]

    daily = daily.sort_values(["symbol", "date"])
    daily["previous_close"] = daily.groupby("symbol")["close"].shift(1)
    prior = daily[["date", "symbol", "previous_close"]]
    bars = bars.merge(
        prior, on=["date", "symbol"], how="left", validate="many_to_one"
    )
    bars = bars.sort_values(["date", "symbol", "bar_start_ts"])
    records: list[dict] = []
    for (date, symbol), group in bars.groupby(["date", "symbol"], sort=False):
        group = group.sort_values("bar_start_ts").reset_index(drop=True)
        if len(group) != 13 or pd.isna(group.loc[0, "previous_close"]):
            continue
        for index in range(12):
            signal_bar = group.iloc[index]
            outcome = group.iloc[index + 1]
            base = (
                float(signal_bar["previous_close"])
                if index == 0
                else float(group.iloc[index - 1]["close"])
            )
            records.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "decision_ts": signal_bar["bar_end_ts"],
                    "available_at_ts": signal_bar["available_at_ts"],
                    "formation_return": float(signal_bar["close"]) / base - 1.0,
                    "entry_ts": outcome["bar_start_ts"],
                    "exit_ts": outcome["bar_end_ts"],
                    "entry": float(outcome["open"]),
                    "exit": float(outcome["close"]),
                    "path_high": float(outcome["high"]),
                    "path_low": float(outcome["low"]),
                    "adjustment_factor": float(signal_bar["adjustment_factor"]),
                }
            )
    panel = pd.DataFrame(records)
    panel = panel.merge(
        features, on=["date", "symbol"], how="left", validate="many_to_one"
    )
    before_features = len(panel)
    panel = panel.dropna(subset=FEATURES).copy()
    panel = panel[panel["date"] >= EVAL_START].copy()
    validate_cutoff(panel)
    if not (pd.to_datetime(panel["available_at_ts"]) <= pd.to_datetime(panel["entry_ts"])).all():
        raise RuntimeError("signal availability occurs after entry")
    if not (pd.to_datetime(panel["exit_ts"]) > pd.to_datetime(panel["entry_ts"])).all():
        raise RuntimeError("nonpositive holding interval")
    diagnostics = {
        "complete_symbol_dates": int(len(complete_keys)),
        "adjusted_symbol_dates": int(len(factors)),
        "decision_rows_before_features": int(before_features),
        "decision_rows_after_features_and_eval": int(len(panel)),
        "decision_row_attrition": int(before_features - len(panel)),
        "symbols_after": int(panel["symbol"].nunique()),
        "dates_after": int(panel["date"].nunique()),
        "adjustment_factor_min": float(factors["adjustment_factor"].min()),
        "adjustment_factor_max": float(factors["adjustment_factor"].max()),
    }
    return panel, diagnostics


def estimate_signals(panel: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    outputs = []
    rejected = 0
    for (_, _), group in panel.groupby(["date", "decision_ts"], sort=True):
        if len(group) < 30:
            rejected += 1
            continue
        group = group.copy()
        lower, upper = group["formation_return"].quantile([0.005, 0.995])
        group["formation_winsor"] = group["formation_return"].clip(lower, upper)
        normalized = pd.DataFrame(
            {
                feature: paper_rank_normalize(group[feature])
                for feature in FEATURES
            },
            index=group.index,
        )
        risk, residual, alpha = source_style_residual(
            group["formation_winsor"], normalized
        )
        group["risk"] = risk
        group["residual"] = residual
        group["cross_section_alpha"] = alpha
        group["residual_decile"] = assign_tail_portfolios(
            group["residual"], groups=10
        )
        group["raw_decile"] = assign_tail_portfolios(
            group["formation_winsor"], groups=10
        )
        if group["residual_decile"].notna().sum() < 20:
            rejected += 1
            continue
        outputs.append(group)
    result = pd.concat(outputs, ignore_index=True) if outputs else panel.iloc[0:0]
    diagnostics = {
        "accepted_cross_sections": int(
            result[["date", "decision_ts"]].drop_duplicates().shape[0]
        ),
        "rejected_cross_sections_lt30_or_invalid": int(rejected),
        "decision_rows": int(len(result)),
        "cross_section_size_min": int(
            result.groupby(["date", "decision_ts"]).size().min()
        ),
        "cross_section_size_median": float(
            result.groupby(["date", "decision_ts"]).size().median()
        ),
        "cross_section_size_max": int(
            result.groupby(["date", "decision_ts"]).size().max()
        ),
    }
    return result, diagnostics


def portfolio_decisions(
    panel: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    decision_rows = []
    position_rows = []
    for model, decile_column in [
        ("residual", "residual_decile"),
        ("raw_return", "raw_decile"),
    ]:
        for (date, decision_ts), group in panel.groupby(
            ["date", "decision_ts"], sort=True
        ):
            low = group[group[decile_column] == 1]
            high = group[group[decile_column] == 10]
            if low.empty or high.empty:
                continue
            for cost in [0.0, 5.0, 10.0]:
                long_values = [
                    long_net_return(row.entry, row.exit, cost)
                    for row in low.itertuples()
                ]
                short_details = [
                    protected_short_net_return(
                        row.entry,
                        row.exit,
                        row.path_high,
                        cost,
                        stop_fraction=0.02,
                        stop_slippage_bps=5.0,
                    )
                    for row in high.itertuples()
                ]
                short_values = [item[0] for item in short_details]
                components = {
                    "long_low": float(np.mean(long_values)),
                    "protected_short_high": float(np.mean(short_values)),
                    "half_long_half_short": float(
                        0.5 * np.mean(long_values) + 0.5 * np.mean(short_values)
                    ),
                }
                directions = (
                    components.keys()
                    if model == "residual"
                    else ["half_long_half_short"]
                )
                for direction in directions:
                    variant = f"{model}_{direction}_{int(cost)}bps"
                    decision_rows.append(
                        {
                            "date": date,
                            "decision_ts": decision_ts,
                            "variant": variant,
                            "model": model,
                            "direction": direction,
                            "cost_bps_per_side": cost,
                            "net_pnl": components[direction],
                            "low_count": len(low),
                            "high_count": len(high),
                            "short_stop_count": int(
                                sum(item[1] for item in short_details)
                            ),
                        }
                    )
                    if direction in {"long_low", "half_long_half_short"}:
                        leg_weight = 1.0 if direction == "long_low" else 0.5
                        for row, pnl in zip(low.itertuples(), long_values):
                            position_rows.append(
                                {
                                    "date": date,
                                    "decision_ts": decision_ts,
                                    "variant": variant,
                                    "symbol": row.symbol,
                                    "leg": "long",
                                    "weight": leg_weight / len(low),
                                    "net_pnl_contribution": leg_weight
                                    * pnl
                                    / len(low),
                                }
                            )
                    if direction in {
                        "protected_short_high",
                        "half_long_half_short",
                    }:
                        leg_weight = (
                            1.0 if direction == "protected_short_high" else 0.5
                        )
                        for row, detail in zip(high.itertuples(), short_details):
                            position_rows.append(
                                {
                                    "date": date,
                                    "decision_ts": decision_ts,
                                    "variant": variant,
                                    "symbol": row.symbol,
                                    "leg": "short",
                                    "weight": leg_weight / len(high),
                                    "net_pnl_contribution": leg_weight
                                    * detail[0]
                                    / len(high),
                                }
                            )
    return pd.DataFrame(decision_rows), pd.DataFrame(position_rows)


def summarize(
    decisions: pd.DataFrame, positions: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    metrics = []
    monthly_rows = []
    concentration_rows = []
    for variant, frame in decisions.groupby("variant"):
        daily = frame.groupby("date", as_index=False)["net_pnl"].sum()
        all_months = pd.period_range(EVAL_START, CUTOFF, freq="M")
        monthly = (
            daily.assign(month=pd.to_datetime(daily["date"]).dt.to_period("M"))
            .groupby("month")["net_pnl"]
            .sum()
            .reindex(all_months, fill_value=0.0)
        )
        dd, recovery, unresolved = max_drawdown_and_recovery(daily)
        row = {
            "variant": variant,
            "model": frame["model"].iloc[0],
            "direction": frame["direction"].iloc[0],
            "cost_bps_per_side": frame["cost_bps_per_side"].iloc[0],
            "full_net_simple_return": float(daily["net_pnl"].sum()),
            "standard_max_drawdown": dd,
            "max_recovery_days": recovery,
            "recovery_unresolved": unresolved,
            "decision_count": int(len(frame)),
            "trading_days": int(daily["date"].nunique()),
            "short_stop_count": int(frame["short_stop_count"].sum()),
            "average_month_18m": float(monthly.mean()),
            "median_month_18m": float(monthly.median()),
            "negative_months_18m": int((monthly < 0).sum()),
            "zero_months_18m": int((monthly.abs() < 1e-12).sum()),
            "average_capital_utilization": 1.0,
            "maximum_gross_exposure": 1.0,
        }
        for label, start in WINDOW_STARTS.items():
            subset = monthly[monthly.index >= start.to_period("M")]
            row[f"average_month_{label}"] = float(subset.mean())
            row[f"negative_months_{label}"] = int((subset < 0).sum())
        metrics.append(row)
        for month, value in monthly.items():
            monthly_rows.append(
                {"variant": variant, "month": str(month), "net_pnl": value}
            )

        p = positions[positions["variant"] == variant]
        by_symbol = (
            p.groupby("symbol")["net_pnl_contribution"].sum().sort_values(
                ascending=False
            )
        )
        total_positive = by_symbol[by_symbol > 0].sum()
        concentration_rows.append(
            {
                "variant": variant,
                "symbols": int(by_symbol.size),
                "top_symbol": by_symbol.index[0] if len(by_symbol) else None,
                "top_symbol_contribution": float(by_symbol.iloc[0])
                if len(by_symbol)
                else np.nan,
                "top_symbol_share_of_positive": float(
                    by_symbol.iloc[0] / total_positive
                )
                if total_positive > 0
                else np.nan,
                "top5_contribution": float(by_symbol.head(5).sum()),
                "leave_top_symbol_out_return": float(
                    row["full_net_simple_return"] - by_symbol.iloc[0]
                )
                if len(by_symbol)
                else np.nan,
            }
        )
    return (
        pd.DataFrame(metrics).sort_values(
            ["average_month_15m", "standard_max_drawdown"],
            ascending=[False, True],
        ),
        pd.DataFrame(monthly_rows),
        pd.DataFrame(concentration_rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--readiness-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    bars = pd.read_parquet(args.readiness_dir / "regular_30m_bars.parquet")
    daily = pd.read_parquet(args.readiness_dir / "daily_split_adjusted.parquet")
    features = pd.read_parquet(args.readiness_dir / "proxy_features.parquet")
    panel, panel_diagnostics = build_decision_panel(bars, daily, features)
    panel, signal_diagnostics = estimate_signals(panel)
    decisions, positions = portfolio_decisions(panel)
    variants, monthly, concentration = summarize(decisions, positions)
    if len(variants) != 12:
        raise RuntimeError(f"expected 12 variants, executed {len(variants)}")
    if panel["date"].max() > CUTOFF or int((panel["date"] >= "2026-05-01").sum()):
        raise RuntimeError("holdout validation failed")

    panel.to_parquet(args.output_dir / "decision_panel.parquet", index=False)
    decisions.to_parquet(
        args.output_dir / "portfolio_decisions.parquet", index=False
    )
    positions.to_parquet(args.output_dir / "positions.parquet", index=False)
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    concentration.to_csv(args.output_dir / "concentration.csv", index=False)
    diagnostics = {
        "status": "passed",
        "label": "adapted proxy residual mechanism; not source replication",
        "max_loaded_date": str(panel["date"].max().date()),
        "holdout_rows_loaded": 0,
        "variant_count": int(len(variants)),
        "panel": panel_diagnostics,
        "signals": signal_diagnostics,
        "bar_screen_execution_limit": (
            "30-minute bars test gross/net economics and adverse stop crossing; "
            "they do not qualify spread, exact stop path, or capacity."
        ),
    }
    contract = {
        "command": (
            "python campaigns/CAM-0004/src/run0002.py "
            "--readiness-dir campaigns/CAM-0004/artifacts/readiness "
            "--output-dir campaigns/CAM-0004/artifacts/RUN-0002"
        ),
        "resolved_defaults": {
            "evaluation_start": "2024-11-01",
            "evaluation_end": "2026-04-30",
            "features": FEATURES,
            "cost_bps_per_side": [0, 5, 10],
            "short_stop_fraction": 0.02,
            "short_stop_slippage_bps": 5.0,
        },
        "executed_variant_count": int(len(variants)),
        "output_paths": [
            "decision_panel.parquet",
            "portfolio_decisions.parquet",
            "positions.parquet",
            "variants.csv",
            "monthly.csv",
            "concentration.csv",
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
    print(variants.to_string(index=False))
    print(json.dumps(diagnostics, indent=2))


if __name__ == "__main__":
    main()
