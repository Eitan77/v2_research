from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0005 import CUTOFF, max_drawdown_and_recovery


PRESSURE = {"all": None, "edge20": 0.20, "edge25": 0.25, "edge33": 0.33}
ACTIVATIONS = {"always": None, "edge10": 10, "edge20": 20, "edge40": 40}
WINDOW_STARTS = {
    "18m": pd.Timestamp("2024-11-01"),
    "15m": pd.Timestamp("2025-02-01"),
    "12m": pd.Timestamp("2025-05-01"),
}


def value_at(
    minutes: pd.DataFrame,
    session: pd.Timestamp,
    minute: str,
    field: str,
) -> float | None:
    row = minutes[
        minutes["symbol"].eq("SMH")
        & minutes["session"].eq(session)
        & minutes["minute"].eq(minute)
    ]
    if len(row) != 1:
        return None
    value = float(row.iloc[0][field])
    return value if np.isfinite(value) and value > 0 else None


def build_states(
    features: pd.DataFrame, minutes: pd.DataFrame
) -> pd.DataFrame:
    frame = features[features["pair"].eq("smh")].sort_values("session").copy()
    outcomes = []
    for item in frame.itertuples():
        entry = value_at(minutes, item.session, "15:59", "open")
        exit_ = value_at(minutes, item.next_session, "09:30", "open")
        if entry is None or exit_ is None:
            outcomes.append(np.nan)
            continue
        raw = exit_ / entry - 1.0
        outcomes.append(raw if item.signal_return < 0 else -raw)
    frame["unlevered_signed_reversal"] = outcomes
    for label, window in ACTIVATIONS.items():
        if window is None:
            frame[f"active_{label}"] = True
        else:
            frame[f"active_{label}"] = (
                frame["unlevered_signed_reversal"]
                .rolling(window, min_periods=window)
                .mean()
                .shift(1)
                .gt(0)
                .fillna(False)
            )
    for label, edge in PRESSURE.items():
        if edge is None:
            frame[f"pressure_{label}"] = True
        else:
            frame[f"pressure_{label}"] = (
                (
                    frame["signal_return"].lt(0)
                    & frame["close_location"].le(edge)
                )
                | (
                    frame["signal_return"].gt(0)
                    & frame["close_location"].ge(1.0 - edge)
                )
            )
    return frame


def apply_states(
    parent: pd.DataFrame, states: pd.DataFrame
) -> pd.DataFrame:
    base = parent[
        parent["threshold"].isin(["q40", "q50", "q60"])
        & parent["entry"].eq("15:59")
        & parent["exit"].isin(["next_open", "next_0934_close"])
        & parent["leg"].isin(
            [
                "both_reversal",
                "negative_signal_bull",
                "positive_signal_inverse",
            ]
        )
        & parent["expression"].eq("product_long_only")
        & parent["cost_bps_per_side"].isin([5, 10])
    ].copy()
    base = base.merge(
        states[
            [
                "session",
                *[f"pressure_{label}" for label in PRESSURE],
                *[f"active_{label}" for label in ACTIVATIONS],
            ]
        ],
        left_on="date",
        right_on="session",
        how="left",
        validate="many_to_one",
    )
    rows = []
    for pressure in PRESSURE:
        for activation in ACTIVATIONS:
            selected = base[
                base[f"pressure_{pressure}"]
                & base[f"active_{activation}"]
            ].copy()
            selected["pressure_state"] = pressure
            selected["activation_state"] = activation
            selected["variant"] = (
                selected["threshold"]
                + "_"
                + pressure
                + "_"
                + activation
                + "_"
                + selected["exit"]
                + "_"
                + selected["leg"]
                + "_c"
                + selected["cost_bps_per_side"].astype(str)
            )
            rows.append(selected)
    return pd.concat(rows, ignore_index=True)


def summarize(
    positions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    month_rows = []
    months = pd.period_range("2024-11", "2026-04", freq="M")
    for variant, frame in positions.groupby("variant"):
        daily = frame.groupby("date", as_index=False)["net_pnl"].sum()
        monthly = (
            daily.assign(month=pd.to_datetime(daily["date"]).dt.to_period("M"))
            .groupby("month")["net_pnl"]
            .sum()
            .reindex(months, fill_value=0.0)
        )
        dd, recovery, unresolved = max_drawdown_and_recovery(daily)
        total = float(daily["net_pnl"].sum())
        dates = sorted(pd.to_datetime(daily["date"]).unique())
        cut = dates[len(dates) // 2]
        row = {
            "variant": variant,
            "threshold": frame["threshold"].iloc[0],
            "pressure_state": frame["pressure_state"].iloc[0],
            "activation_state": frame["activation_state"].iloc[0],
            "exit": frame["exit"].iloc[0],
            "leg": frame["leg"].iloc[0],
            "cost_bps_per_side": int(frame["cost_bps_per_side"].iloc[0]),
            "full_net_simple_return": total,
            "standard_max_drawdown": dd,
            "max_recovery_days": recovery,
            "recovery_unresolved": unresolved,
            "trade_count": int(len(frame)),
            "win_rate": float(frame["net_pnl"].gt(0).mean()),
            "early_half_net": float(
                frame[pd.to_datetime(frame["date"]) < cut]["net_pnl"].sum()
            ),
            "late_half_net": float(
                frame[pd.to_datetime(frame["date"]) >= cut]["net_pnl"].sum()
            ),
            "top_5_day_profit_share": (
                float(daily["net_pnl"].nlargest(5).sum() / total)
                if total > 0
                else np.nan
            ),
        }
        for label, start in WINDOW_STARTS.items():
            subset = monthly[monthly.index >= start.to_period("M")]
            row[f"average_month_{label}"] = float(subset.mean())
            row[f"negative_months_{label}"] = int((subset < 0).sum())
            row[f"zero_months_{label}"] = int((subset == 0).sum())
        rows.append(row)
        for month, value in monthly.items():
            month_rows.append(
                {"variant": variant, "month": str(month), "net_pnl": value}
            )
    return (
        pd.DataFrame(rows).sort_values(
            ["average_month_15m", "standard_max_drawdown"],
            ascending=[False, True],
        ),
        pd.DataFrame(month_rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--readiness-dir", type=Path, required=True)
    parser.add_argument("--features-path", type=Path, required=True)
    parser.add_argument("--parent-positions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    minutes = pd.read_parquet(args.readiness_dir / "targeted_minutes.parquet")
    minutes["session"] = pd.to_datetime(minutes["session"])
    features = pd.read_parquet(args.features_path)
    features["session"] = pd.to_datetime(features["session"])
    features["next_session"] = pd.to_datetime(features["next_session"])
    parent = pd.read_parquet(args.parent_positions)
    parent["date"] = pd.to_datetime(parent["date"])
    states = build_states(features, minutes)
    positions = apply_states(parent, states)
    variants, monthly = summarize(positions)
    if len(variants) != 576:
        raise RuntimeError(f"expected 576 variants, executed {len(variants)}")
    if pd.to_datetime(positions["date"]).max() > CUTOFF:
        raise RuntimeError("cutoff failed")
    positions.to_parquet(args.output_dir / "positions.parquet", index=False)
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    diagnostics = {
        "status": "passed",
        "max_loaded_date": str(minutes["session"].max().date()),
        "holdout_rows_loaded": 0,
        "position_rows": int(len(positions)),
        "variant_count": int(len(variants)),
        "activation_source": "shifted trailing unlevered signed SMH reversal",
    }
    (args.output_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "command": (
            "python campaigns/CAM-0005/src/run0004.py "
            "--readiness-dir campaigns/CAM-0005/artifacts/readiness "
            "--features-path campaigns/CAM-0005/artifacts/RUN-0002/features.parquet "
            "--parent-positions campaigns/CAM-0005/artifacts/RUN-0003/positions.parquet "
            "--output-dir campaigns/CAM-0005/artifacts/RUN-0004"
        ),
        "resolved_defaults": {
            "thresholds": ["q40", "q50", "q60"],
            "pressure_states": list(PRESSURE),
            "activation_states": list(ACTIVATIONS),
            "legs": [
                "both_reversal",
                "negative_signal_bull",
                "positive_signal_inverse",
            ],
            "entry": "15:59",
            "exits": ["next_open", "next_0934_close"],
            "cost_bps_per_side": [5, 10],
        },
        "executed_variant_count": int(len(variants)),
        "output_paths": [
            "positions.parquet",
            "variants.csv",
            "monthly.csv",
            "contract.json",
            "diagnostics.json",
        ],
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(variants.head(50).to_string(index=False))


if __name__ == "__main__":
    main()
