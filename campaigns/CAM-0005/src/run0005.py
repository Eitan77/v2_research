from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0005 import CUTOFF
from run0004 import PRESSURE, summarize, value_at


WINDOWS = {"always": None, "event10": 10, "event20": 20, "event40": 40}


def build_leg_states(
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
    frame["leg_edge"] = outcomes
    frame["signal_leg"] = np.where(
        frame["signal_return"].lt(0), "negative", "positive"
    )
    for label, window in WINDOWS.items():
        if window is None:
            frame[f"active_{label}"] = True
            continue
        frame[f"active_{label}"] = False
        for leg in ["negative", "positive"]:
            mask = frame["signal_leg"].eq(leg)
            active = (
                frame.loc[mask, "leg_edge"]
                .rolling(window, min_periods=window)
                .mean()
                .shift(1)
                .gt(0)
                .fillna(False)
            )
            frame.loc[mask, f"active_{label}"] = active.values
    for label, edge in PRESSURE.items():
        if label == "all":
            continue
        frame[f"pressure_{label}"] = (
            (frame["signal_return"].lt(0) & frame["close_location"].le(edge))
            | (
                frame["signal_return"].gt(0)
                & frame["close_location"].ge(1.0 - edge)
            )
        )
    return frame


def apply_leg_states(
    parent: pd.DataFrame, states: pd.DataFrame
) -> pd.DataFrame:
    base = parent[
        parent["threshold"].isin(["q50", "q60"])
        & parent["entry"].eq("15:59")
        & parent["exit"].isin(["next_open", "next_0934_close"])
        & parent["leg"].eq("both_reversal")
        & parent["expression"].eq("product_long_only")
        & parent["cost_bps_per_side"].isin([5, 10])
    ].copy()
    columns = [
        "session",
        *[f"pressure_{label}" for label in ["edge20", "edge25", "edge33"]],
        *[f"active_{label}" for label in WINDOWS],
    ]
    base = base.merge(
        states[columns],
        left_on="date",
        right_on="session",
        how="left",
        validate="many_to_one",
    )
    rows = []
    for pressure in ["edge20", "edge25", "edge33"]:
        for activation in WINDOWS:
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
                + "_leg_specific_c"
                + selected["cost_bps_per_side"].astype(str)
            )
            rows.append(selected)
    return pd.concat(rows, ignore_index=True)


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
    states = build_leg_states(features, minutes)
    positions = apply_leg_states(parent, states)
    variants, monthly = summarize(positions)
    if len(variants) != 96:
        raise RuntimeError(f"expected 96 variants, executed {len(variants)}")
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
        "activation_source": (
            "shifted trailing same-sign unlevered SMH reversal events"
        ),
    }
    (args.output_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "command": (
            "python campaigns/CAM-0005/src/run0005.py "
            "--readiness-dir campaigns/CAM-0005/artifacts/readiness "
            "--features-path campaigns/CAM-0005/artifacts/RUN-0002/features.parquet "
            "--parent-positions campaigns/CAM-0005/artifacts/RUN-0003/positions.parquet "
            "--output-dir campaigns/CAM-0005/artifacts/RUN-0005"
        ),
        "resolved_defaults": {
            "thresholds": ["q50", "q60"],
            "pressure_states": ["edge20", "edge25", "edge33"],
            "leg_activation": list(WINDOWS),
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
