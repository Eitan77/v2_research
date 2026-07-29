from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from cam0009 import allocate_intraday, protected_short_return
from run0001 import CUTOFF, metrics, sha256


LENGTHS = (1, 5, 10, 15, 30)
MULTIPLIERS = (0.75, 1.0, 1.5)
VOLUME_MINIMUMS = (1.0, 1.5, 2.5)
LEADER_LIQUIDITY_MINIMUMS = (100_000_000, 250_000_000, 1_000_000_000)
COOLDOWNS = (0, 15, 30, 60)
COST = 5


def make_path_cache(minutes: pd.DataFrame) -> dict:
    cache = {}
    for (symbol, date), frame in minutes.groupby(["symbol", "date"], sort=False):
        ordered = frame.sort_values("minute_number")
        numbers = ordered["minute_number"].to_numpy(dtype=np.int32)
        opens = ordered["open"].to_numpy(dtype=float)
        highs = ordered["high"].to_numpy(dtype=float)
        cache[(str(symbol), pd.Timestamp(date))] = (
            numbers,
            opens,
            highs,
            int(ordered["session_close_minute"].iloc[0]),
        )
    return cache


def path_return(
    cache: dict,
    symbol: str,
    date: pd.Timestamp,
    entry_minute: int,
    direction: int,
) -> tuple[float, bool, float, str] | None:
    item = cache.get((symbol, pd.Timestamp(date)))
    if item is None:
        return None
    numbers, opens, highs, close_minute = item
    exit_minute = close_minute - 1
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
    exit_price = float(opens[stop])
    if direction < 0:
        unit_return, stopped, effective_exit = protected_short_return(
            entry,
            exit_price,
            highs[start:stop].tolist(),
            0.02,
            COST,
            10,
        )
    else:
        unit_return = exit_price / entry - 1 - 2 * COST / 10_000
        stopped = False
        effective_exit = exit_price
    return unit_return, stopped, effective_exit, str(exit_minute)


def leaders_with_cooldown(leaders: pd.DataFrame, cooldown: int) -> pd.DataFrame:
    if cooldown == 0 or leaders.empty:
        return leaders
    keep = []
    for _, date_frame in leaders.groupby("date", sort=True):
        last = -10_000
        for index, row in date_frame.sort_values("bucket_start").iterrows():
            bucket = int(row["bucket_start"])
            if bucket - last >= cooldown:
                keep.append(index)
                last = bucket
    return leaders.loc[keep]


def prepare_leaders(
    base: pd.DataFrame,
    residual_minimum: float,
    volume_minimum: float,
    leader_liquidity_minimum: float,
) -> tuple[pd.DataFrame, int]:
    residual = "smh_residual"
    leader_pool = base[
        base["prior20_median_dollar_volume"].ge(leader_liquidity_minimum)
        & base["volume_surprise"].ge(volume_minimum)
        & base[residual].abs().ge(residual_minimum)
    ].copy()
    leader_pool["absolute_residual"] = leader_pool[residual].abs()
    leaders = (
        leader_pool.sort_values(
            ["date", "bucket_start", "absolute_residual", "symbol"],
            ascending=[True, True, False, True],
        )
        .groupby(["date", "bucket_start"], sort=False)
        .head(1)
    )
    return leaders, int(len(leader_pool))


def select_variant(
    base: pd.DataFrame,
    base_lookup: pd.DataFrame,
    path_cache: dict,
    path_result_cache: dict,
    peer_cache: dict,
    length: int,
    leaders_before_cooldown: pd.DataFrame,
    leader_pool_rows: int,
    cooldown: int,
) -> tuple[pd.DataFrame, dict]:
    residual = "smh_residual"
    leaders = leaders_before_cooldown
    leaders = leaders_with_cooldown(leaders, cooldown)
    rows = []
    missing_paths = 0
    for leader in leaders.itertuples(index=False):
        leader_residual = float(getattr(leader, residual))
        direction = 1 if leader_residual > 0 else -1
        peer_key = (
            length,
            pd.Timestamp(leader.date),
            int(leader.bucket_start),
            str(leader.symbol),
        )
        peers = peer_cache.get(peer_key)
        if peers is None:
            frame = base_lookup.loc[
                (pd.Timestamp(leader.date), int(leader.bucket_start))
            ]
            if isinstance(frame, pd.Series):
                frame = frame.to_frame().T
            peers = frame[frame["symbol"].ne(leader.symbol)].copy()
            peers["peer_signed_ratio"] = (
                direction * peers[residual] / abs(leader_residual)
            )
            peers = peers[
                peers["peer_signed_ratio"].between(-0.25, 0.50)
            ].sort_values(
                ["prior20_median_dollar_volume", "symbol"],
                ascending=[False, True],
            ).head(3)
            peer_cache[peer_key] = peers
        event_id = (
            f"{length}|{pd.Timestamp(leader.date).date()}|"
            f"{int(leader.bucket_start)}|{leader.symbol}"
        )
        for peer in peers.itertuples(index=False):
            path_key = (
                str(peer.symbol),
                pd.Timestamp(leader.date),
                int(peer.entry_minute),
                direction,
            )
            if path_key not in path_result_cache:
                path_result_cache[path_key] = path_return(
                    path_cache,
                    str(peer.symbol),
                    pd.Timestamp(leader.date),
                    int(peer.entry_minute),
                    direction,
                )
            path = path_result_cache[path_key]
            if path is None:
                missing_paths += 1
                continue
            unit_return, stopped, effective_exit, exit_minute = path
            rows.append(
                {
                    "event_id": event_id,
                    "date": pd.Timestamp(leader.date),
                    "formation_length": length,
                    "formation_start": int(leader.bucket_start),
                    "entry_minute": int(peer.entry_minute),
                    "leader_symbol": str(leader.symbol),
                    "leader_residual": leader_residual,
                    "leader_volume_surprise": float(leader.volume_surprise),
                    "symbol": str(peer.symbol),
                    "peer_residual": float(getattr(peer, residual)),
                    "peer_signed_ratio": float(peer.peer_signed_ratio),
                    "prior20_median_dollar_volume": float(
                        peer.prior20_median_dollar_volume
                    ),
                    "trade_direction": "long" if direction > 0 else "short",
                    "entry_raw": float(peer.entry_open_raw),
                    "exit_minute": int(exit_minute),
                    "effective_exit_raw": float(effective_exit),
                    "entry_timestamp": pd.Timestamp(leader.date)
                    + pd.Timedelta(minutes=int(peer.entry_minute)),
                    "exit_timestamp": pd.Timestamp(leader.date)
                    + pd.Timedelta(minutes=int(exit_minute)),
                    "unit_net_return": float(unit_return),
                    "stopped": bool(stopped),
                }
            )
    attrition = {
        "eligible_peer_rows": int(len(base)),
        "leader_pool_rows": leader_pool_rows,
        "selected_events_before_cooldown": int(len(leaders_before_cooldown)),
        "selected_events_after_cooldown": int(len(leaders)),
        "executable_peer_candidates": int(len(rows)),
        "missing_or_incomplete_paths": int(missing_paths),
    }
    return pd.DataFrame(rows), attrition


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record", type=Path, required=True)
    parser.add_argument("--features-dir", type=Path, required=True)
    parser.add_argument("--minute", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    record = yaml.safe_load(args.run_record.read_text(encoding="utf-8"))
    if record["status"] != "frozen":
        raise RuntimeError("RUN-0003 record is not frozen")
    if record["frozen_configuration"]["expected_variant_count"]["total"] != 540:
        raise RuntimeError("Frozen variant count mismatch")

    minutes = pd.read_parquet(
        args.minute,
        columns=[
            "symbol", "date", "minute_number", "open", "high",
            "session_close_minute",
        ],
    )
    minutes["date"] = pd.to_datetime(minutes["date"])
    if minutes["date"].max() > CUTOFF:
        raise RuntimeError("RUN-0003 minute input crosses sealed boundary")
    sessions = pd.DatetimeIndex(sorted(minutes["date"].unique()))
    path_cache = make_path_cache(minutes)
    path_result_cache = {}

    metric_rows = []
    trade_frames = []
    daily_frames = []
    attrition = {}
    feature_hashes = {}
    maximum_date = minutes["date"].max()
    for length in LENGTHS:
        feature_path = args.features_dir / f"formation_{length}m.parquet"
        features = pd.read_parquet(
            feature_path,
            columns=[
                "symbol", "date", "bucket_start", "entry_minute",
                "entry_open_raw", "entry_complete", "volume_surprise",
                "prior20_median_dollar_volume", "pit_member", "smh_residual",
            ],
        )
        features["date"] = pd.to_datetime(features["date"])
        maximum_date = max(maximum_date, features["date"].max())
        if maximum_date > CUTOFF:
            raise RuntimeError("RUN-0003 feature input crosses sealed boundary")
        feature_hashes[str(length)] = sha256(feature_path)
        base = features[
            features["pit_member"]
            & features["entry_complete"]
            & features["prior20_median_dollar_volume"].ge(100_000_000)
            & features["smh_residual"].notna()
            & features["bucket_start"].ge(575)
        ].copy()
        base_lookup = base.set_index(["date", "bucket_start"]).sort_index()
        peer_cache = {}
        base_threshold = 0.0045 * math.sqrt(length)
        for multiplier in MULTIPLIERS:
            threshold = base_threshold * multiplier
            for volume_minimum in VOLUME_MINIMUMS:
                for liquidity_minimum in LEADER_LIQUIDITY_MINIMUMS:
                    leaders, leader_pool_rows = prepare_leaders(
                        base,
                        threshold,
                        volume_minimum,
                        liquidity_minimum,
                    )
                    for cooldown in COOLDOWNS:
                        variant_id = (
                            f"f{length}__m{multiplier:g}__v{volume_minimum:g}__"
                            f"liq{int(liquidity_minimum/1_000_000)}m__"
                            f"cd{cooldown}__5bp"
                        )
                        selected, counts = select_variant(
                            base,
                            base_lookup,
                            path_cache,
                            path_result_cache,
                            peer_cache,
                            length,
                            leaders,
                            leader_pool_rows,
                            cooldown,
                        )
                        if selected.empty:
                            selected = pd.DataFrame(
                                columns=[
                                    "event_id", "date", "leader_symbol", "symbol",
                                    "stopped", "entry_timestamp", "exit_timestamp",
                                    "unit_net_return",
                                ]
                            )
                            selected["position_fraction"] = pd.Series(dtype=float)
                            selected["trade_pnl"] = pd.Series(dtype=float)
                        else:
                            selected = allocate_intraday(
                                selected, 0.10, 0.10, 1.0
                            )
                            selected["trade_pnl"] = (
                                selected["unit_net_return"]
                                * selected["position_fraction"]
                            )
                        selected["variant_id"] = variant_id
                        daily_values = (
                            selected.groupby("date")["trade_pnl"].sum()
                            if len(selected)
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
                            "both",
                            "close",
                            COST,
                            selected,
                            daily,
                        )
                        row.update(
                            {
                                "formation_length": length,
                                "threshold_multiplier": multiplier,
                                "absolute_residual_minimum": threshold,
                                "volume_surprise_minimum": volume_minimum,
                                "leader_liquidity_minimum": liquidity_minimum,
                                "cooldown_minutes": cooldown,
                                **counts,
                            }
                        )
                        metric_rows.append(row)
                        if len(selected):
                            trade_frames.append(selected)
                        daily_frames.append(daily)
        del features, base, base_lookup

    metric_frame = pd.DataFrame(metric_rows)
    if len(metric_frame) != 540:
        raise RuntimeError(f"Executed {len(metric_frame)} rather than 540 variants")
    metric_frame.to_parquet(args.output_dir / "variant_metrics.parquet", index=False)
    pd.concat(trade_frames, ignore_index=True).to_parquet(
        args.output_dir / "trade_details.parquet", index=False
    )
    pd.concat(daily_frames, ignore_index=True).to_parquet(
        args.output_dir / "daily_pnl.parquet", index=False
    )
    attrition = metric_frame[
        [
            "variant_id", "eligible_peer_rows", "leader_pool_rows",
            "selected_events_before_cooldown", "selected_events_after_cooldown",
            "executable_peer_candidates", "missing_or_incomplete_paths",
        ]
    ]
    attrition.to_parquet(args.output_dir / "attrition.parquet", index=False)
    reconciliation = {
        "status": "passed",
        "run_id": "RUN-0003",
        "expected_variant_count": 540,
        "executed_variant_count": int(len(metric_frame)),
        "frozen_configuration_hash": hashlib.sha256(
            yaml.safe_dump(record["frozen_configuration"], sort_keys=True).encode()
        ).hexdigest(),
        "input_hashes": {
            "minute": sha256(args.minute),
            "features": feature_hashes,
        },
        "executed_code_hashes": {
            "run0003.py": sha256(Path(__file__)),
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
