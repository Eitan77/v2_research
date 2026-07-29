from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import yaml

from cam0009 import allocate_intraday
from run0001 import CUTOFF, metrics, sha256


DIRECTIONS = {
    "positive_only": ("long",),
    "negative_only": ("short",),
    "both": ("long", "short"),
}
TIMES = {
    "all": (575, 930),
    "open_hour": (575, 629),
    "post_open": (630, 930),
    "mid_morning": (600, 719),
}
VOLUMES = {
    "all": (1.5, None),
    "moderate": (1.5, 6.5),
    "focused": (2.0, 6.5),
}
MAGNITUDES = {
    "all": (0.01, None),
    "moderate": (0.01, 0.02),
    "strong": (0.0125, 0.025),
}
RATIOS = {
    "baseline": (-0.25, 0.50),
    "strict_lag": (-0.10, 0.10),
    "not_opposite": (0.00, 0.50),
    "partly_caught_up": (0.10, 0.50),
}
PEER_COUNTS = (1, 3)
COSTS = (2, 5, 10)


def bounded(frame: pd.DataFrame, column: str, bounds: tuple) -> pd.DataFrame:
    low, high = bounds
    mask = frame[column].ge(low)
    if high is not None:
        mask &= frame[column].le(high)
    return frame[mask]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record", type=Path, required=True)
    parser.add_argument("--parent-trades", type=Path, required=True)
    parser.add_argument("--minute", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    record = yaml.safe_load(args.run_record.read_text(encoding="utf-8"))
    if record["status"] != "frozen":
        raise RuntimeError("RUN-0002 record is not frozen")
    if record["frozen_configuration"]["expected_variant_count"]["total"] != 2592:
        raise RuntimeError("Frozen variant count mismatch")

    trades = pd.read_parquet(args.parent_trades)
    base = trades[trades["variant_id"].eq("smh__both__close__2bp")].copy()
    base["date"] = pd.to_datetime(base["date"])
    base["absolute_leader_residual"] = base["leader_residual"].abs()
    base = base.drop(columns=["position_fraction", "trade_pnl", "variant_id"])
    minutes = pd.read_parquet(args.minute, columns=["date"])
    minutes["date"] = pd.to_datetime(minutes["date"])
    if max(base["date"].max(), minutes["date"].max()) > CUTOFF:
        raise RuntimeError("RUN-0002 input crosses sealed boundary")
    sessions = pd.DatetimeIndex(sorted(minutes["date"].unique()))

    metric_rows = []
    trade_frames = []
    daily_frames = []
    for direction, allowed in DIRECTIONS.items():
        direction_base = base[base["trade_direction"].isin(allowed)]
        for time_name, (time_low, time_high) in TIMES.items():
            time_base = direction_base[
                direction_base["formation_start"].between(time_low, time_high)
            ]
            for volume_name, volume_bounds in VOLUMES.items():
                volume_base = bounded(
                    time_base,
                    "leader_volume_surprise",
                    volume_bounds,
                )
                for magnitude_name, magnitude_bounds in MAGNITUDES.items():
                    magnitude_base = bounded(
                        volume_base,
                        "absolute_leader_residual",
                        magnitude_bounds,
                    )
                    for ratio_name, ratio_bounds in RATIOS.items():
                        ratio_base = bounded(
                            magnitude_base,
                            "peer_signed_ratio",
                            ratio_bounds,
                        )
                        for peer_count in PEER_COUNTS:
                            selected = (
                                ratio_base.sort_values(
                                    [
                                        "event_id",
                                        "prior20_median_dollar_volume",
                                        "symbol",
                                    ],
                                    ascending=[True, False, True],
                                )
                                .groupby("event_id", sort=False)
                                .head(peer_count)
                                .copy()
                            )
                            for cost in COSTS:
                                variant_id = (
                                    f"{direction}__{time_name}__{volume_name}__"
                                    f"{magnitude_name}__{ratio_name}__p{peer_count}__"
                                    f"{cost}bp"
                                )
                                variant = selected.copy()
                                variant["unit_net_return"] = (
                                    variant["unit_net_return"]
                                    - 2 * (cost - 2) / 10_000
                                )
                                variant = allocate_intraday(
                                    variant,
                                    position_cap=0.10,
                                    symbol_cap=0.10,
                                    gross_cap=1.0,
                                )
                                variant["trade_pnl"] = (
                                    variant["unit_net_return"]
                                    * variant["position_fraction"]
                                )
                                variant["variant_id"] = variant_id
                                daily_values = (
                                    variant.groupby("date")["trade_pnl"].sum()
                                    if len(variant)
                                    else pd.Series(dtype=float)
                                )
                                daily = pd.DataFrame({"date": sessions})
                                daily["net_pnl"] = (
                                    daily["date"].map(daily_values).fillna(0.0)
                                )
                                daily["variant_id"] = variant_id
                                row = metrics(
                                    variant_id,
                                    "smh",
                                    direction,
                                    "close",
                                    cost,
                                    variant,
                                    daily,
                                )
                                row.update(
                                    {
                                        "time_filter": time_name,
                                        "volume_filter": volume_name,
                                        "magnitude_filter": magnitude_name,
                                        "ratio_filter": ratio_name,
                                        "maximum_peers": peer_count,
                                        "parent_candidate_peers": int(len(base)),
                                        "retained_candidate_share": (
                                            float(len(variant) / len(base))
                                            if len(base)
                                            else 0.0
                                        ),
                                    }
                                )
                                metric_rows.append(row)
                                trade_frames.append(variant)
                                daily_frames.append(daily)

    metric_frame = pd.DataFrame(metric_rows)
    if len(metric_frame) != 2592:
        raise RuntimeError(
            f"Executed {len(metric_frame)} variants rather than 2592"
        )
    metric_frame.to_parquet(args.output_dir / "variant_metrics.parquet", index=False)
    pd.concat(trade_frames, ignore_index=True).to_parquet(
        args.output_dir / "trade_details.parquet", index=False
    )
    pd.concat(daily_frames, ignore_index=True).to_parquet(
        args.output_dir / "daily_pnl.parquet", index=False
    )

    reconciliation = {
        "status": "passed",
        "run_id": "RUN-0002",
        "expected_variant_count": 2592,
        "executed_variant_count": int(len(metric_frame)),
        "parent_candidates": int(len(base)),
        "frozen_configuration_hash": hashlib.sha256(
            yaml.safe_dump(
                record["frozen_configuration"], sort_keys=True
            ).encode()
        ).hexdigest(),
        "input_hashes": {
            "parent_trade_details": sha256(args.parent_trades),
            "minute": sha256(args.minute),
        },
        "executed_code_hashes": {
            "run0002.py": sha256(Path(__file__)),
            "cam0009.py": sha256(Path(__file__).with_name("cam0009.py")),
            "run0001.py": sha256(Path(__file__).with_name("run0001.py")),
        },
        "maximum_loaded_date": str(
            max(base["date"].max(), minutes["date"].max()).date()
        ),
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2), encoding="utf-8"
    )
    top = metric_frame.sort_values(
        [
            "recent_15m_average_month",
            "recent_12m_average_month",
            "maximum_drawdown",
        ],
        ascending=[False, False, True],
    ).head(50)
    summary = {
        "status": "completed_uninterpreted",
        "variant_count": int(len(metric_frame)),
        "top_50": top.to_dict(orient="records"),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(reconciliation, indent=2))
    print(
        top[
            [
                "variant_id",
                "recent_15m_average_month",
                "recent_12m_average_month",
                "full_average_month",
                "maximum_drawdown",
                "recent_15m_positive_months",
                "recent_15m_negative_months",
                "allocated_trades",
                "retained_candidate_share",
            ]
        ].head(20).to_string(index=False)
    )


if __name__ == "__main__":
    main()
