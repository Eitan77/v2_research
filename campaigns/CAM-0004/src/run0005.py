import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0004 import CUTOFF, max_drawdown_and_recovery
from run0004 import build_signals


KS = [4, 6]
MS = [2, 3, 4, 6]
COSTS = [0, 1, 3]
WINDOW_STARTS = {
    "18m": pd.Timestamp("2024-11-01"),
    "15m": pd.Timestamp("2025-02-01"),
    "12m": pd.Timestamp("2025-05-01"),
}


def weighted_mean(values: pd.Series, scores: pd.Series, mode: str) -> float:
    if mode == "equal":
        return float(values.mean())
    weights = scores.abs().astype(float)
    cap = float(weights.quantile(0.90))
    weights = weights.clip(upper=cap)
    if not np.isfinite(weights.sum()) or weights.sum() <= 0:
        return float(values.mean())
    return float(np.average(values, weights=weights))


def apply_round_trip_cost(gross_return: float, cost_bps: int) -> float:
    return gross_return - 2.0 * cost_bps / 10_000.0


def make_decisions(signals: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "date",
        "symbol",
        "decision_ts",
        "high_market_vol",
        *[f"score_K{k}" for k in KS],
        *[f"decile_K{k}" for k in KS],
    ]
    outcomes = outcomes.merge(
        signals[columns],
        on=["date", "symbol", "decision_ts"],
        how="inner",
        validate="many_to_one",
    )
    outcomes = outcomes[outcomes["high_market_vol"]].copy()
    rows = []
    for k in KS:
        score_col = f"score_K{k}"
        decile_col = f"decile_K{k}"
        for m in MS:
            base = outcomes[outcomes["horizon"].eq(m)]
            for breadth, low_cut, high_cut in [
                ("decile", 1, 10),
                ("quintile", 2, 9),
            ]:
                for (date, decision_ts), group in base.groupby(
                    ["date", "decision_ts"], sort=True
                ):
                    low = group[group[decile_col].le(low_cut)]
                    high = group[group[decile_col].ge(high_cut)]
                    if low.empty or high.empty:
                        continue
                    for weight_mode in ["equal", "strength_capped"]:
                        long_gross = weighted_mean(
                            low["actionable_long"], low[score_col], weight_mode
                        )
                        short_gross = weighted_mean(
                            high["protected_short"],
                            high[score_col],
                            weight_mode,
                        )
                        for cost in COSTS:
                            long_net = apply_round_trip_cost(long_gross, cost)
                            short_net = apply_round_trip_cost(short_gross, cost)
                            legs = {
                                "long_low": long_net,
                                "protected_short_high": short_net,
                                "half_long_short": 0.5 * long_net
                                + 0.5 * short_net,
                            }
                            for leg, unscaled in legs.items():
                                rows.append(
                                    {
                                        "date": date,
                                        "decision_ts": decision_ts,
                                        "variant": (
                                            f"K{k}_M{m}_{breadth}_{leg}_"
                                            f"{weight_mode}_c{cost}"
                                        ),
                                        "K": k,
                                        "M": m,
                                        "breadth": breadth,
                                        "leg": leg,
                                        "weight": weight_mode,
                                        "cost_bps_per_side": cost,
                                        "net_pnl": unscaled / m,
                                        "unscaled_net_pnl": unscaled,
                                        "low_count": len(low),
                                        "high_count": len(high),
                                        "stop_count": int(high["stopped"].sum()),
                                    }
                                )
    return pd.DataFrame(rows)


def summarize(
    decisions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    metrics = []
    monthly_rows = []
    months = pd.period_range("2024-11", "2026-04", freq="M")
    for variant, frame in decisions.groupby("variant"):
        daily = frame.groupby("date", as_index=False)["net_pnl"].sum()
        monthly = (
            daily.assign(month=pd.to_datetime(daily["date"]).dt.to_period("M"))
            .groupby("month")["net_pnl"]
            .sum()
            .reindex(months, fill_value=0.0)
        )
        dd, recovery, unresolved = max_drawdown_and_recovery(daily)
        sorted_daily = daily["net_pnl"].sort_values(ascending=False)
        total = float(daily["net_pnl"].sum())
        row = {
            "variant": variant,
            "K": int(frame["K"].iloc[0]),
            "M": int(frame["M"].iloc[0]),
            "breadth": frame["breadth"].iloc[0],
            "leg": frame["leg"].iloc[0],
            "weight": frame["weight"].iloc[0],
            "cost_bps_per_side": int(frame["cost_bps_per_side"].iloc[0]),
            "full_net_simple_return": total,
            "mean_decision_net_bps_scaled": float(frame["net_pnl"].mean())
            * 10_000.0,
            "standard_max_drawdown": dd,
            "max_recovery_days": recovery,
            "recovery_unresolved": unresolved,
            "decision_count": int(len(frame)),
            "trading_days": int(daily["date"].nunique()),
            "stop_count": int(frame["stop_count"].sum()),
            "top_5_day_profit_share": (
                float(sorted_daily.head(5).sum() / total)
                if total > 0
                else np.nan
            ),
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
    return (
        pd.DataFrame(metrics).sort_values(
            ["average_month_15m", "standard_max_drawdown"],
            ascending=[False, True],
        ),
        pd.DataFrame(monthly_rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-dir", type=Path, required=True)
    parser.add_argument("--outcomes-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    panel = pd.read_parquet(args.parent_dir / "decision_panel.parquet")
    outcomes = pd.read_parquet(args.outcomes_path)
    signals = build_signals(panel)
    decisions = make_decisions(signals, outcomes)
    variants, monthly = summarize(decisions)
    if len(variants) != 288:
        raise RuntimeError(f"expected 288 variants, executed {len(variants)}")
    dates = pd.to_datetime(decisions["date"])
    if dates.max() > CUTOFF or int((dates >= "2026-05-01").sum()):
        raise RuntimeError("holdout validation failed")
    decisions.to_parquet(
        args.output_dir / "portfolio_decisions.parquet", index=False
    )
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    diagnostics = {
        "status": "passed",
        "max_loaded_date": str(dates.max().date()),
        "holdout_rows_loaded": 0,
        "decision_rows": int(len(decisions)),
        "variant_count": int(len(variants)),
        "capital_scaling": "each M-period cohort divided by M",
        "execution_limit": "protected 30-minute bar screen, not quote qualified",
    }
    (args.output_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "command": (
            "python campaigns/CAM-0004/src/run0005.py "
            "--parent-dir campaigns/CAM-0004/artifacts/RUN-0002 "
            "--outcomes-path campaigns/CAM-0004/artifacts/RUN-0003/outcomes.parquet "
            "--output-dir campaigns/CAM-0004/artifacts/RUN-0005"
        ),
        "resolved_defaults": {
            "K": KS,
            "M": MS,
            "breadth": ["decile", "quintile"],
            "state": "high_market_vol",
            "legs": [
                "long_low",
                "protected_short_high",
                "half_long_short",
            ],
            "weights": ["equal", "strength_capped"],
            "cost_bps_per_side": COSTS,
            "cohort_capital_scale": "1/M",
        },
        "executed_variant_count": int(len(variants)),
        "output_paths": [
            "portfolio_decisions.parquet",
            "variants.csv",
            "monthly.csv",
            "contract.json",
            "diagnostics.json",
        ],
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(variants.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
