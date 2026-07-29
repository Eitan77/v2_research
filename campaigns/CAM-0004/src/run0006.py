import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0004 import (
    CUTOFF,
    max_drawdown_and_recovery,
    paper_rank_normalize,
    source_style_residual,
)
from run0004 import build_signals
from run0005 import apply_round_trip_cost, weighted_mean


KS = [4, 6]
MS = [2, 3, 4, 6]
MODELS = {
    "beta_only": ["beta_60d"],
    "price_liquidity": ["log_price", "log_dollar_volume"],
    "return_risk": [
        "reversal_1d",
        "momentum_5d",
        "momentum_20d",
        "volatility_20d",
        "beta_60d",
    ],
}
WINDOW_STARTS = {
    "18m": pd.Timestamp("2024-11-01"),
    "15m": pd.Timestamp("2025-02-01"),
    "12m": pd.Timestamp("2025-05-01"),
}


def add_model_residuals(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.copy()
    pieces = []
    for _, group in panel.groupby(["date", "decision_ts"], sort=True):
        group = group.copy()
        for model, features in MODELS.items():
            normalized = pd.DataFrame(
                {
                    feature: paper_rank_normalize(group[feature])
                    for feature in features
                },
                index=group.index,
            )
            _, residual, _ = source_style_residual(
                group["formation_winsor"], normalized
            )
            group[f"residual_{model}"] = residual
        group["residual_full_proxy"] = group["residual"]
        pieces.append(group)
    return pd.concat(pieces, ignore_index=True)


def add_formation_scores(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.sort_values(["date", "symbol", "decision_ts"]).copy()
    for model in ["full_proxy", *MODELS]:
        residual_col = f"residual_{model}"
        grouped = panel.groupby(["date", "symbol"], sort=False)[residual_col]
        for k in KS:
            score_col = f"score_{model}_K{k}"
            panel[score_col] = grouped.transform(
                lambda x, k=k: (1.0 + x).rolling(k, min_periods=k).apply(
                    np.prod, raw=True
                )
                - 1.0
            )
            panel[f"decile_{model}_K{k}"] = panel.groupby(
                ["date", "decision_ts"]
            )[score_col].transform(
                lambda x: pd.qcut(
                    x.rank(method="first"),
                    10,
                    labels=False,
                    duplicates="drop",
                )
                + 1
                if x.notna().sum() >= 30
                else np.nan
            )
    return panel


def make_decisions(panel: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    state = build_signals(panel)[
        ["date", "symbol", "decision_ts", "high_market_vol"]
    ]
    panel = panel.merge(
        state,
        on=["date", "symbol", "decision_ts"],
        how="left",
        validate="one_to_one",
    )
    score_columns = [
        column
        for column in panel.columns
        if column.startswith("score_") or column.startswith("decile_")
    ]
    outcomes = outcomes.merge(
        panel[["date", "symbol", "decision_ts", "high_market_vol", *score_columns]],
        on=["date", "symbol", "decision_ts"],
        how="inner",
        validate="many_to_one",
    )
    outcomes = outcomes[outcomes["high_market_vol"]]
    rows = []
    for model in ["full_proxy", *MODELS]:
        for k in KS:
            score_col = f"score_{model}_K{k}"
            decile_col = f"decile_{model}_K{k}"
            for m in MS:
                base = outcomes[outcomes["horizon"].eq(m)]
                for breadth, low_cut in [("decile", 1), ("quintile", 2)]:
                    low = base[base[decile_col].le(low_cut)]
                    for (date, decision_ts), group in low.groupby(
                        ["date", "decision_ts"], sort=True
                    ):
                        for weight in ["equal", "strength_capped"]:
                            gross = weighted_mean(
                                group["actionable_long"],
                                group[score_col],
                                weight,
                            )
                            for cost in [1, 3]:
                                rows.append(
                                    {
                                        "date": date,
                                        "decision_ts": decision_ts,
                                        "variant": (
                                            f"{model}_K{k}_M{m}_{breadth}_"
                                            f"{weight}_c{cost}"
                                        ),
                                        "model": model,
                                        "K": k,
                                        "M": m,
                                        "breadth": breadth,
                                        "weight": weight,
                                        "cost_bps_per_side": cost,
                                        "net_pnl": apply_round_trip_cost(
                                            gross, cost
                                        )
                                        / m,
                                        "position_count": len(group),
                                    }
                                )
    return pd.DataFrame(rows)


def summarize(
    decisions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    metrics = []
    month_rows = []
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
        total = float(daily["net_pnl"].sum())
        top = float(daily["net_pnl"].nlargest(5).sum())
        row = {
            "variant": variant,
            "model": frame["model"].iloc[0],
            "K": int(frame["K"].iloc[0]),
            "M": int(frame["M"].iloc[0]),
            "breadth": frame["breadth"].iloc[0],
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
            "top_5_day_profit_share": top / total if total > 0 else np.nan,
        }
        for label, start in WINDOW_STARTS.items():
            subset = monthly[monthly.index >= start.to_period("M")]
            row[f"average_month_{label}"] = float(subset.mean())
            row[f"negative_months_{label}"] = int((subset < 0).sum())
        metrics.append(row)
        for month, value in monthly.items():
            month_rows.append(
                {"variant": variant, "month": str(month), "net_pnl": value}
            )
    return (
        pd.DataFrame(metrics).sort_values(
            ["average_month_15m", "standard_max_drawdown"],
            ascending=[False, True],
        ),
        pd.DataFrame(month_rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-dir", type=Path, required=True)
    parser.add_argument("--outcomes-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    panel = pd.read_parquet(args.parent_dir / "decision_panel.parquet")
    panel = add_model_residuals(panel)
    panel = add_formation_scores(panel)
    outcomes = pd.read_parquet(args.outcomes_path)
    decisions = make_decisions(panel, outcomes)
    variants, monthly = summarize(decisions)
    if len(variants) != 256:
        raise RuntimeError(f"expected 256 variants, executed {len(variants)}")
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
        "exact_source_model_tested": False,
        "models_are_labeled_adaptations": True,
    }
    (args.output_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "command": (
            "python campaigns/CAM-0004/src/run0006.py "
            "--parent-dir campaigns/CAM-0004/artifacts/RUN-0002 "
            "--outcomes-path campaigns/CAM-0004/artifacts/RUN-0003/outcomes.parquet "
            "--output-dir campaigns/CAM-0004/artifacts/RUN-0006"
        ),
        "resolved_defaults": {
            "models": ["full_proxy", *MODELS],
            "K": KS,
            "M": MS,
            "breadth": ["decile", "quintile"],
            "weights": ["equal", "strength_capped"],
            "cost_bps_per_side": [1, 3],
            "state": "high_market_vol",
            "direction": "actionable_long_low",
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
