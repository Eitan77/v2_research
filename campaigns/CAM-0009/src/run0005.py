from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from cam0009 import protected_long_return, protected_short_return
from run0001 import CUTOFF, metrics, sha256
from run0004 import PARENT_VARIANT, make_path_cache


DIRECTIONS = {
    "positive_only": ("long",),
    "negative_only": ("short",),
    "both": ("long", "short"),
}
EXPRESSIONS = ("unhedged", "half_dollar_smh", "dollar_neutral_smh", "shifted_beta_smh")
PEER_COUNTS = (1, 3)
SIGNAL_CAPS = (0.10, 0.20, 1 / 3, 0.50)
COSTS = (2, 5, 10)


def exact_leg_returns(
    path_cache: dict,
    symbol: str,
    date: pd.Timestamp,
    entry_minute: int,
    exit_minute: int,
) -> dict | None:
    item = path_cache.get((symbol, pd.Timestamp(date)))
    if item is None:
        return None
    numbers, opens, highs, lows, _ = item
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
    long_result = protected_long_return(
        entry, planned_exit, lows[start:stop].tolist(), None, 0, 10
    )
    short_result = protected_short_return(
        entry, planned_exit, highs[start:stop].tolist(), 0.02, 0, 10
    )
    return {
        "long_gross": long_result[0],
        "long_stopped": long_result[1],
        "short_gross": short_result[0],
        "short_stopped": short_result[1],
        "entry_raw": entry,
        "exit_raw": planned_exit,
    }


def allocate_pairs(
    candidates: pd.DataFrame,
    signal_cap: float,
    peer_symbol_cap: float = 1 / 3,
    hedge_cap: float = 0.50,
    gross_cap: float = 1.0,
) -> pd.DataFrame:
    result = candidates.sort_values(
        ["entry_timestamp", "symbol", "leader_symbol"]
    ).copy()
    result["pair_gross"] = 0.0
    result["peer_fraction"] = 0.0
    result["hedge_fraction"] = 0.0
    active = []
    for timestamp, indices in result.groupby("entry_timestamp", sort=True).groups.items():
        entry = pd.Timestamp(timestamp)
        active = [item for item in active if item["exit"] > entry]
        total_active = sum(item["pair"] for item in active)
        available = max(0.0, gross_cap - total_active)
        if available <= 1e-12:
            continue
        cohort_target = min(signal_cap, available / len(indices))
        peer_active = defaultdict(float)
        hedge_active = 0.0
        for item in active:
            peer_active[item["symbol"]] += item["peer"]
            hedge_active += item["hedge"]
        for index in indices:
            ratio = float(result.loc[index, "hedge_ratio"])
            symbol = str(result.loc[index, "symbol"])
            size = min(cohort_target, max(0.0, gross_cap - total_active))
            size = min(
                size,
                max(0.0, peer_symbol_cap - peer_active[symbol]) * (1 + ratio),
            )
            if ratio > 0:
                size = min(
                    size,
                    max(0.0, hedge_cap - hedge_active) * (1 + ratio) / ratio,
                )
            if size <= 1e-12:
                continue
            peer = size / (1 + ratio)
            hedge = size - peer
            result.loc[index, ["pair_gross", "peer_fraction", "hedge_fraction"]] = [
                size, peer, hedge
            ]
            active.append(
                {
                    "exit": pd.Timestamp(result.loc[index, "exit_timestamp"]),
                    "symbol": symbol,
                    "pair": size,
                    "peer": peer,
                    "hedge": hedge,
                }
            )
            total_active += size
            peer_active[symbol] += peer
            hedge_active += hedge
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record", type=Path, required=True)
    parser.add_argument("--parent-trades", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--minute", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    record = yaml.safe_load(args.run_record.read_text(encoding="utf-8"))
    if record["status"] != "frozen":
        raise RuntimeError("Expression-audit run record is not frozen")
    if record["frozen_configuration"]["expected_variant_count"]["total"] != 288:
        raise RuntimeError("Frozen variant count mismatch")

    parent = pd.read_parquet(
        args.parent_trades,
        filters=[("variant_id", "==", PARENT_VARIANT)],
    ).copy()
    parent["date"] = pd.to_datetime(parent["date"])
    parent["candidate_id"] = parent["event_id"] + "|" + parent["symbol"]
    features = pd.read_parquet(
        args.features,
        columns=["symbol", "date", "bucket_start", "beta_smh"],
    )
    features["date"] = pd.to_datetime(features["date"])
    parent = parent.merge(
        features.rename(columns={"bucket_start": "formation_start"})[
            ["symbol", "date", "formation_start", "beta_smh"]
        ],
        on=["symbol", "date", "formation_start"],
        how="left",
        validate="many_to_one",
    )
    minutes = pd.read_parquet(
        args.minute,
        columns=[
            "symbol", "date", "minute_number", "open", "high", "low",
            "session_close_minute",
        ],
    )
    minutes["date"] = pd.to_datetime(minutes["date"])
    maximum_date = max(parent["date"].max(), features["date"].max(), minutes["date"].max())
    if maximum_date > CUTOFF:
        raise RuntimeError("RUN-0005 input crosses sealed boundary")
    sessions = pd.DatetimeIndex(sorted(minutes["date"].unique()))
    path_cache = make_path_cache(minutes)

    rows = []
    missing_peer = 0
    missing_hedge = 0
    missing_beta = 0
    for candidate in parent.itertuples(index=False):
        entry_minute = int(candidate.entry_minute) + 3
        exit_minute = int(candidate.exit_minute)
        peer = exact_leg_returns(
            path_cache, str(candidate.symbol), candidate.date,
            entry_minute, exit_minute,
        )
        hedge = exact_leg_returns(
            path_cache, "SMH", candidate.date, entry_minute, exit_minute
        )
        if peer is None:
            missing_peer += 1
            continue
        if hedge is None:
            missing_hedge += 1
        if pd.isna(candidate.beta_smh):
            missing_beta += 1
        row = candidate._asdict()
        row.update(
            {
                "entry_timestamp": pd.Timestamp(candidate.date)
                + pd.Timedelta(minutes=entry_minute),
                "exit_timestamp": pd.Timestamp(candidate.date)
                + pd.Timedelta(minutes=exit_minute),
                "peer_long_gross": peer["long_gross"],
                "peer_short_gross": peer["short_gross"],
                "peer_short_stopped": peer["short_stopped"],
                "hedge_path_available": hedge is not None,
                "hedge_long_gross": (
                    hedge["long_gross"] if hedge is not None else np.nan
                ),
                "hedge_short_gross": (
                    hedge["short_gross"] if hedge is not None else np.nan
                ),
                "hedge_short_stopped": (
                    hedge["short_stopped"] if hedge is not None else False
                ),
            }
        )
        rows.append(row)
    executable = pd.DataFrame(rows)

    metric_rows = []
    trade_frames = []
    daily_frames = []
    attrition_rows = []
    for direction, allowed in DIRECTIONS.items():
        direction_base = executable[
            executable["trade_direction"].isin(allowed)
        ]
        for expression in EXPRESSIONS:
            expression_base = direction_base.copy()
            if expression == "unhedged":
                expression_base["hedge_ratio"] = 0.0
            elif expression == "half_dollar_smh":
                expression_base = expression_base[
                    expression_base["hedge_path_available"]
                ].copy()
                expression_base["hedge_ratio"] = 0.5
            elif expression == "dollar_neutral_smh":
                expression_base = expression_base[
                    expression_base["hedge_path_available"]
                ].copy()
                expression_base["hedge_ratio"] = 1.0
            else:
                expression_base = expression_base[
                    expression_base["hedge_path_available"]
                    & expression_base["beta_smh"].notna()
                ].copy()
                expression_base["hedge_ratio"] = expression_base[
                    "beta_smh"
                ].clip(0.25, 2.0)
            for peer_count in PEER_COUNTS:
                selected = (
                    expression_base.sort_values(
                        ["event_id", "prior20_median_dollar_volume", "symbol"],
                        ascending=[True, False, True],
                    )
                    .groupby("event_id", sort=False)
                    .head(peer_count)
                    .copy()
                )
                for signal_cap in SIGNAL_CAPS:
                    allocated = allocate_pairs(selected, signal_cap)
                    allocated = allocated[allocated["pair_gross"].gt(0)].copy()
                    for cost in COSTS:
                        variant_id = (
                            f"{direction}__{expression}__p{peer_count}__"
                            f"cap{signal_cap:.4f}__{cost}bp"
                        )
                        variant = allocated.copy()
                        is_long = variant["trade_direction"].eq("long")
                        peer_return = np.where(
                            is_long,
                            variant["peer_long_gross"],
                            variant["peer_short_gross"],
                        ) - 2 * cost / 10_000
                        hedge_return = np.where(
                            is_long,
                            variant["hedge_short_gross"],
                            variant["hedge_long_gross"],
                        ) - 2 * cost / 10_000
                        if expression == "unhedged":
                            variant["trade_pnl"] = (
                                variant["peer_fraction"] * peer_return
                            )
                        else:
                            variant["trade_pnl"] = (
                                variant["peer_fraction"] * peer_return
                                + variant["hedge_fraction"] * hedge_return
                            )
                        variant["position_fraction"] = variant["pair_gross"]
                        variant["unit_net_return"] = (
                            variant["trade_pnl"] / variant["pair_gross"]
                        )
                        variant["stopped"] = np.where(
                            is_long,
                            variant["hedge_short_stopped"]
                            & variant["hedge_fraction"].gt(0),
                            variant["peer_short_stopped"],
                        ).astype(bool)
                        variant["variant_id"] = variant_id
                        daily_values = variant.groupby("date")["trade_pnl"].sum()
                        daily = pd.DataFrame({"date": sessions})
                        daily["net_pnl"] = (
                            daily["date"].map(daily_values).fillna(0.0)
                        )
                        daily["variant_id"] = variant_id
                        row = metrics(
                            variant_id, "smh", direction, "close", cost,
                            variant, daily,
                        )
                        row.update(
                            {
                                "hedge_expression": expression,
                                "maximum_peers": peer_count,
                                "signal_total_gross_cap": signal_cap,
                                "average_pair_gross": float(
                                    variant["pair_gross"].mean()
                                ) if len(variant) else 0.0,
                                "average_peer_fraction": float(
                                    variant["peer_fraction"].mean()
                                ) if len(variant) else 0.0,
                                "average_hedge_fraction": float(
                                    variant["hedge_fraction"].mean()
                                ) if len(variant) else 0.0,
                            }
                        )
                        metric_rows.append(row)
                        trade_frames.append(variant)
                        daily_frames.append(daily)
                        attrition_rows.append(
                            {
                                "variant_id": variant_id,
                                "parent_candidates": int(len(parent)),
                                "path_beta_complete_candidates": int(
                                    len(executable)
                                ),
                                "selected_candidates": int(len(selected)),
                                "allocated_trades": int(len(variant)),
                            }
                        )

    metric_frame = pd.DataFrame(metric_rows)
    if len(metric_frame) != 288:
        raise RuntimeError(f"Executed {len(metric_frame)} rather than 288 variants")
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
    reconciliation = {
        "status": "passed",
        "run_id": str(record["run_id"]),
        "expected_variant_count": 288,
        "executed_variant_count": int(len(metric_frame)),
        "parent_candidates": int(len(parent)),
        "path_beta_complete_candidates": int(len(executable)),
        "missing_peer_paths": missing_peer,
        "missing_hedge_paths": missing_hedge,
        "missing_shifted_betas": missing_beta,
        "frozen_configuration_hash": hashlib.sha256(
            yaml.safe_dump(record["frozen_configuration"], sort_keys=True).encode()
        ).hexdigest(),
        "input_hashes": {
            "parent_trade_details": sha256(args.parent_trades),
            "features": sha256(args.features),
            "minute": sha256(args.minute),
        },
        "executed_code_hashes": {
            "run0005.py": sha256(Path(__file__)),
            "cam0009.py": sha256(Path(__file__).with_name("cam0009.py")),
        },
        "maximum_loaded_date": str(maximum_date.date()),
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2), encoding="utf-8"
    )
    executable.to_parquet(
        args.output_dir / "executable_candidates.parquet", index=False
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
