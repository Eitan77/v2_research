from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from cam0009 import (
    allocate_intraday,
    protected_long_return,
    protected_short_return,
)
from run0001 import CUTOFF, metrics, sha256


PARENT_VARIANT = "f5__m0.75__v1.5__liq250m__cd15__5bp"
DIRECTIONS = {
    "positive_only": ("long",),
    "negative_only": ("short",),
    "both": ("long", "short"),
}
DELAYS = (0, 1, 3)
HORIZONS = ("30", "60", "120", "close")
RATIOS = {
    "baseline": (-0.25, 0.50),
    "strict_lag": (-0.10, 0.10),
    "not_opposite": (0.00, 0.50),
    "partly_caught_up": (0.10, 0.50),
}
PEER_COUNTS = (1, 3)
LONG_STOPS = (None, 0.02, 0.04)
COSTS = (2, 5, 10)


def make_path_cache(minutes: pd.DataFrame) -> dict:
    cache = {}
    for (symbol, date), frame in minutes.groupby(["symbol", "date"], sort=False):
        ordered = frame.sort_values("minute_number")
        cache[(str(symbol), pd.Timestamp(date))] = (
            ordered["minute_number"].to_numpy(dtype=np.int32),
            ordered["open"].to_numpy(dtype=float),
            ordered["high"].to_numpy(dtype=float),
            ordered["low"].to_numpy(dtype=float),
            int(ordered["session_close_minute"].iloc[0]),
        )
    return cache


def execution_row(candidate, path_cache: dict, delay: int, horizon: str):
    item = path_cache.get((str(candidate.symbol), pd.Timestamp(candidate.date)))
    if item is None:
        return None
    numbers, opens, highs, lows, close_minute = item
    entry_minute = int(candidate.entry_minute) + delay
    exit_minute = (
        close_minute - 1 if horizon == "close"
        else entry_minute + int(horizon)
    )
    if exit_minute >= close_minute:
        return None
    start = int(np.searchsorted(numbers, entry_minute))
    stop = int(np.searchsorted(numbers, exit_minute))
    if (
        start >= len(numbers)
        or stop >= len(numbers)
        or numbers[start] != entry_minute
        or numbers[stop] != exit_minute
    ):
        return None
    expected = exit_minute - entry_minute + 1
    if stop - start + 1 != expected or np.any(np.diff(numbers[start:stop + 1]) != 1):
        return None
    entry = float(opens[start])
    planned_exit = float(opens[stop])
    long_none = protected_long_return(
        entry, planned_exit, lows[start:stop].tolist(), None, 0, 10
    )
    long_2 = protected_long_return(
        entry, planned_exit, lows[start:stop].tolist(), 0.02, 0, 10
    )
    long_4 = protected_long_return(
        entry, planned_exit, lows[start:stop].tolist(), 0.04, 0, 10
    )
    short_2 = protected_short_return(
        entry, planned_exit, highs[start:stop].tolist(), 0.02, 0, 10
    )
    return {
        "candidate_id": candidate.candidate_id,
        "entry_minute_exec": entry_minute,
        "exit_minute_exec": exit_minute,
        "entry_raw_exec": entry,
        "entry_timestamp": pd.Timestamp(candidate.date)
        + pd.Timedelta(minutes=entry_minute),
        "exit_timestamp": pd.Timestamp(candidate.date)
        + pd.Timedelta(minutes=exit_minute),
        "long_none_gross": long_none[0],
        "long_none_stopped": long_none[1],
        "long_2_gross": long_2[0],
        "long_2_stopped": long_2[1],
        "long_4_gross": long_4[0],
        "long_4_stopped": long_4[1],
        "short_2_gross": short_2[0],
        "short_2_stopped": short_2[1],
    }


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
        raise RuntimeError("RUN-0004 record is not frozen")
    if record["frozen_configuration"]["expected_variant_count"]["total"] != 2592:
        raise RuntimeError("Frozen variant count mismatch")

    parent = pd.read_parquet(
        args.parent_trades,
        filters=[("variant_id", "==", PARENT_VARIANT)],
    ).copy()
    parent["date"] = pd.to_datetime(parent["date"])
    parent["candidate_id"] = parent["event_id"] + "|" + parent["symbol"]
    if parent["candidate_id"].duplicated().any():
        raise RuntimeError("Parent candidates are not unique")
    minutes = pd.read_parquet(
        args.minute,
        columns=[
            "symbol", "date", "minute_number", "open", "high", "low",
            "session_close_minute",
        ],
    )
    minutes["date"] = pd.to_datetime(minutes["date"])
    maximum_date = max(parent["date"].max(), minutes["date"].max())
    if maximum_date > CUTOFF:
        raise RuntimeError("RUN-0004 input crosses sealed boundary")
    sessions = pd.DatetimeIndex(sorted(minutes["date"].unique()))
    path_cache = make_path_cache(minutes)

    execution = {}
    execution_attrition = {}
    for delay in DELAYS:
        for horizon in HORIZONS:
            rows = []
            for candidate in parent.itertuples(index=False):
                row = execution_row(candidate, path_cache, delay, horizon)
                if row is not None:
                    rows.append(row)
            key = (delay, horizon)
            execution[key] = pd.DataFrame(rows)
            execution_attrition[f"d{delay}__h{horizon}"] = {
                "parent_candidates": int(len(parent)),
                "executable_candidates": int(len(rows)),
                "missing_or_after_close": int(len(parent) - len(rows)),
            }

    metric_rows = []
    trade_frames = []
    daily_frames = []
    attrition_rows = []
    for direction, allowed in DIRECTIONS.items():
        direction_base = parent[parent["trade_direction"].isin(allowed)]
        for ratio_name, (ratio_low, ratio_high) in RATIOS.items():
            ratio_base = direction_base[
                direction_base["peer_signed_ratio"].between(ratio_low, ratio_high)
            ]
            for peer_count in PEER_COUNTS:
                selected = (
                    ratio_base.sort_values(
                        ["event_id", "prior20_median_dollar_volume", "symbol"],
                        ascending=[True, False, True],
                    )
                    .groupby("event_id", sort=False)
                    .head(peer_count)
                    .copy()
                )
                static_columns = [
                    "candidate_id", "event_id", "date", "formation_start",
                    "leader_symbol", "leader_residual",
                    "leader_volume_surprise", "symbol", "peer_residual",
                    "peer_signed_ratio", "prior20_median_dollar_volume",
                    "trade_direction",
                ]
                for delay in DELAYS:
                    for horizon in HORIZONS:
                        executable = selected[static_columns].merge(
                            execution[(delay, horizon)],
                            on="candidate_id",
                            how="inner",
                            validate="one_to_one",
                        )
                        if len(executable):
                            allocated_base = allocate_intraday(
                                executable, 0.10, 0.10, 1.0
                            )
                        else:
                            allocated_base = executable.copy()
                            allocated_base["position_fraction"] = pd.Series(
                                dtype=float
                            )
                        allocated_base = allocated_base[
                            allocated_base["position_fraction"].gt(0)
                        ].copy()
                        for long_stop in LONG_STOPS:
                            stop_label = (
                                "none" if long_stop is None
                                else str(int(long_stop * 100))
                            )
                            long_prefix = (
                                "long_none" if long_stop is None
                                else f"long_{int(long_stop * 100)}"
                            )
                            for cost in COSTS:
                                variant_id = (
                                    f"{direction}__d{delay}__h{horizon}__"
                                    f"{ratio_name}__p{peer_count}__"
                                    f"ls{stop_label}__{cost}bp"
                                )
                                variant = allocated_base.copy()
                                is_long = variant["trade_direction"].eq("long")
                                variant["unit_net_return"] = np.where(
                                    is_long,
                                    variant[f"{long_prefix}_gross"],
                                    variant["short_2_gross"],
                                ) - 2 * cost / 10_000
                                variant["stopped"] = np.where(
                                    is_long,
                                    variant[f"{long_prefix}_stopped"],
                                    variant["short_2_stopped"],
                                ).astype(bool)
                                variant["trade_pnl"] = (
                                    variant["unit_net_return"]
                                    * variant["position_fraction"]
                                )
                                variant["variant_id"] = variant_id
                                daily_values = variant.groupby("date")[
                                    "trade_pnl"
                                ].sum()
                                daily = pd.DataFrame({"date": sessions})
                                daily["net_pnl"] = (
                                    daily["date"].map(daily_values).fillna(0.0)
                                )
                                daily["variant_id"] = variant_id
                                row = metrics(
                                    variant_id,
                                    "smh",
                                    direction,
                                    horizon,
                                    cost,
                                    variant,
                                    daily,
                                )
                                row.update(
                                    {
                                        "additional_entry_delay": delay,
                                        "ratio_filter": ratio_name,
                                        "maximum_peers": peer_count,
                                        "long_stop_fraction": long_stop,
                                        "parent_candidates": int(len(parent)),
                                        "filter_retained_candidates": int(
                                            len(selected)
                                        ),
                                        "executable_candidates": int(
                                            len(executable)
                                        ),
                                    }
                                )
                                metric_rows.append(row)
                                trade_frames.append(variant)
                                daily_frames.append(daily)
                                attrition_rows.append(
                                    {
                                        "variant_id": variant_id,
                                        "parent_candidates": int(len(parent)),
                                        "filter_retained_candidates": int(
                                            len(selected)
                                        ),
                                        "executable_candidates": int(
                                            len(executable)
                                        ),
                                        "allocated_trades": int(len(variant)),
                                    }
                                )

    metric_frame = pd.DataFrame(metric_rows)
    if len(metric_frame) != 2592:
        raise RuntimeError(
            f"Executed {len(metric_frame)} rather than 2592 variants"
        )
    metric_frame.to_parquet(args.output_dir / "variant_metrics.parquet", index=False)
    pd.concat(trade_frames, ignore_index=True).to_parquet(
        args.output_dir / "allocated_trade_details.parquet", index=False
    )
    pd.concat(daily_frames, ignore_index=True).to_parquet(
        args.output_dir / "daily_pnl.parquet", index=False
    )
    pd.DataFrame(attrition_rows).to_parquet(
        args.output_dir / "attrition.parquet", index=False
    )
    (args.output_dir / "execution_attrition.json").write_text(
        json.dumps(execution_attrition, indent=2), encoding="utf-8"
    )
    reconciliation = {
        "status": "passed",
        "run_id": "RUN-0004",
        "expected_variant_count": 2592,
        "executed_variant_count": int(len(metric_frame)),
        "parent_candidates": int(len(parent)),
        "frozen_configuration_hash": hashlib.sha256(
            yaml.safe_dump(record["frozen_configuration"], sort_keys=True).encode()
        ).hexdigest(),
        "input_hashes": {
            "parent_trade_details": sha256(args.parent_trades),
            "minute": sha256(args.minute),
        },
        "executed_code_hashes": {
            "run0004.py": sha256(Path(__file__)),
            "cam0009.py": sha256(Path(__file__).with_name("cam0009.py")),
            "run0001.py": sha256(Path(__file__).with_name("run0001.py")),
        },
        "maximum_loaded_date": str(maximum_date.date()),
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2), encoding="utf-8"
    )
    top = metric_frame.sort_values(
        ["recent_15m_average_month", "recent_12m_average_month"],
        ascending=False,
    ).head(50)
    (args.output_dir / "summary.json").write_text(
        json.dumps(
            {
                "status": "completed_uninterpreted",
                "variant_count": int(len(metric_frame)),
                "top_50": top.to_dict(orient="records"),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(json.dumps(reconciliation, indent=2))
    print(
        top[
            [
                "variant_id", "recent_15m_average_month",
                "recent_12m_average_month", "full_average_month",
                "maximum_drawdown", "recent_15m_positive_months",
                "recent_15m_negative_months", "allocated_trades",
            ]
        ].head(20).to_string(index=False)
    )


if __name__ == "__main__":
    main()
