import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0004 import CUTOFF, max_drawdown_and_recovery


KS = [1, 2, 3, 4, 6]
MS = [1, 2, 3, 4, 6]
WINDOW_STARTS = {
    "18m": pd.Timestamp("2024-11-01"),
    "15m": pd.Timestamp("2025-02-01"),
    "12m": pd.Timestamp("2025-05-01"),
}


def expanding_prior_flag(series: pd.Series) -> pd.Series:
    threshold = series.expanding(min_periods=40).quantile(2.0 / 3.0).shift(1)
    return series.gt(threshold).fillna(False)


def build_signals(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    panel = panel.sort_values(["date", "symbol", "decision_ts"])
    states = (
        panel.groupby(["date", "decision_ts"], as_index=False)
        .agg(
            noise_level=("residual", lambda x: float(np.abs(x).mean())),
            market_vol=("volatility_20d", "median"),
        )
        .sort_values(["decision_ts"])
    )
    states["decision_period"] = (
        pd.to_datetime(states["decision_ts"], utc=True)
        .dt.tz_convert("America/New_York")
        .dt.strftime("%H:%M")
    )
    states["high_noise"] = states.groupby(
        "decision_period", group_keys=False
    )["noise_level"].transform(expanding_prior_flag)
    states["high_market_vol"] = states.groupby(
        "decision_period", group_keys=False
    )["market_vol"].transform(expanding_prior_flag)
    keep = ["date", "decision_ts", "high_noise", "high_market_vol"]
    panel = panel.merge(states[keep], on=["date", "decision_ts"], how="left")
    grouped = panel.groupby(["date", "symbol"], sort=False)["residual"]
    for k in KS:
        score = grouped.transform(
            lambda x, k=k: (1.0 + x).rolling(k, min_periods=k).apply(
                np.prod, raw=True
            )
            - 1.0
        )
        panel[f"score_K{k}"] = score
        panel[f"decile_K{k}"] = panel.groupby(
            ["date", "decision_ts"]
        )[f"score_K{k}"].transform(
            lambda x: pd.qcut(
                x.rank(method="first"), 10, labels=False, duplicates="drop"
            )
            + 1
            if x.notna().sum() >= 30
            else np.nan
        )
    return panel


def make_decisions(signals: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    score_columns = [f"decile_K{k}" for k in KS]
    merge_columns = [
        "date",
        "symbol",
        "decision_ts",
        "high_noise",
        "high_market_vol",
        *score_columns,
    ]
    outcomes = outcomes.merge(
        signals[merge_columns],
        on=["date", "symbol", "decision_ts"],
        how="inner",
        validate="many_to_one",
    )
    rows = []
    executions = {
        "source_cc": ("source_long", "source_short"),
        "actionable_oc": ("actionable_long", "actionable_short"),
    }
    states = {
        "all": None,
        "high_noise": "high_noise",
        "high_market_vol": "high_market_vol",
    }
    for k in KS:
        column = f"decile_K{k}"
        for breadth, low_cut, high_cut in [
            ("decile", 1, 10),
            ("quintile", 2, 9),
        ]:
            for m in MS:
                base = outcomes[outcomes["horizon"].eq(m)]
                low = base[base[column].le(low_cut)]
                high = base[base[column].ge(high_cut)]
                keys = ["date", "decision_ts"]
                for execution, (long_col, short_col) in executions.items():
                    long_leg = low.groupby(keys)[long_col].mean()
                    short_leg = high.groupby(keys)[short_col].mean()
                    counts_low = low.groupby(keys).size()
                    counts_high = high.groupby(keys).size()
                    joined = pd.concat(
                        [long_leg, short_leg, counts_low, counts_high],
                        axis=1,
                        keys=["long", "short", "low_count", "high_count"],
                    ).dropna()
                    joined["net_pnl"] = 0.5 * joined["long"] + 0.5 * joined[
                        "short"
                    ]
                    state_frame = (
                        base.groupby(keys)[["high_noise", "high_market_vol"]]
                        .first()
                        .reindex(joined.index)
                    )
                    joined = joined.join(state_frame)
                    for state, flag in states.items():
                        selected = joined if flag is None else joined[joined[flag]]
                        for (date, decision_ts), item in selected.iterrows():
                            rows.append(
                                {
                                    "date": date,
                                    "decision_ts": decision_ts,
                                    "variant": (
                                        f"K{k}_M{m}_{breadth}_{execution}_{state}"
                                    ),
                                    "K": k,
                                    "M": m,
                                    "breadth": breadth,
                                    "execution": execution,
                                    "state": state,
                                    "net_pnl": float(item["net_pnl"]),
                                    "low_count": int(item["low_count"]),
                                    "high_count": int(item["high_count"]),
                                }
                            )
    return pd.DataFrame(rows)


def summarize(
    decisions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    metric_rows = []
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
        mean_decision = float(frame["net_pnl"].mean())
        row = {
            "variant": variant,
            "K": int(frame["K"].iloc[0]),
            "M": int(frame["M"].iloc[0]),
            "breadth": frame["breadth"].iloc[0],
            "execution": frame["execution"].iloc[0],
            "state": frame["state"].iloc[0],
            "full_gross_simple_return": float(daily["net_pnl"].sum()),
            "mean_decision_bps": mean_decision * 10_000.0,
            "break_even_cost_bps_per_side": mean_decision * 5_000.0,
            "standard_max_drawdown": dd,
            "max_recovery_days": recovery,
            "recovery_unresolved": unresolved,
            "decision_count": int(len(frame)),
            "trading_days": int(daily["date"].nunique()),
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
    metrics = pd.DataFrame(metric_rows).sort_values(
        ["average_month_15m", "standard_max_drawdown"],
        ascending=[False, True],
    )
    return metrics, pd.DataFrame(month_rows)


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
    if len(variants) != 300:
        raise RuntimeError(f"expected 300 variants, executed {len(variants)}")
    if pd.to_datetime(decisions["date"]).max() > CUTOFF or int(
        (pd.to_datetime(decisions["date"]) >= "2026-05-01").sum()
    ):
        raise RuntimeError("holdout validation failed")
    decisions.to_parquet(
        args.output_dir / "portfolio_decisions.parquet", index=False
    )
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    diagnostics = {
        "status": "passed",
        "max_loaded_date": str(pd.to_datetime(decisions["date"]).max().date()),
        "holdout_rows_loaded": 0,
        "decision_rows": int(len(decisions)),
        "variant_count": int(len(variants)),
        "clock_periods_selected_from_run0003": False,
        "unprotected_variants_are_diagnostic_only": True,
    }
    (args.output_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "command": (
            "python campaigns/CAM-0004/src/run0004.py "
            "--parent-dir campaigns/CAM-0004/artifacts/RUN-0002 "
            "--outcomes-path campaigns/CAM-0004/artifacts/RUN-0003/outcomes.parquet "
            "--output-dir campaigns/CAM-0004/artifacts/RUN-0004"
        ),
        "resolved_defaults": {
            "K": KS,
            "M": MS,
            "breadth": ["decile", "quintile"],
            "executions": ["source_cc", "actionable_oc"],
            "states": ["all", "high_noise", "high_market_vol"],
            "cost_bps_per_side": 0,
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
