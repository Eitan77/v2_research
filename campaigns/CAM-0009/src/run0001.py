from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from cam0009 import (
    allocate_intraday,
    max_drawdown_and_recovery,
    protected_short_return,
)


CUTOFF = pd.Timestamp("2026-04-30")
MODES = ("raw", "qqq", "smh")
DIRECTIONS = ("positive_only", "negative_only", "both")
HORIZONS = ("5", "15", "30", "60", "close")
COSTS = (2, 5, 10)
WINDOWS = {
    "full": pd.Timestamp("2024-07-01"),
    "recent_18m": pd.Timestamp("2024-11-01"),
    "recent_15m": pd.Timestamp("2025-02-01"),
    "recent_12m": pd.Timestamp("2025-05-01"),
}
BLOCKS = {
    "block_1_2024h2": (pd.Timestamp("2024-07-01"), pd.Timestamp("2024-12-31")),
    "block_2_2025h1": (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-06-30")),
    "block_3_2025h2": (pd.Timestamp("2025-07-01"), pd.Timestamp("2025-12-31")),
    "block_4_2026ytd": (pd.Timestamp("2026-01-01"), CUTOFF),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clock(date: pd.Timestamp, minute_number: int) -> pd.Timestamp:
    return pd.Timestamp(date) + pd.Timedelta(minutes=int(minute_number))


def select_events(features: pd.DataFrame, mode: str) -> pd.DataFrame:
    residual_column = f"{mode}_residual"
    base = features[
        features["pit_member"]
        & features["entry_complete"]
        & features["prior20_median_dollar_volume"].ge(100_000_000)
        & features[residual_column].notna()
        & features["bucket_start"].between(575, 930)
    ].copy()
    rows: list[dict] = []
    for date, date_frame in base.groupby("date", sort=True):
        last_accepted = -10_000
        for bucket, frame in date_frame.groupby("bucket_start", sort=True):
            bucket = int(bucket)
            if bucket - last_accepted < 30:
                continue
            leader_pool = frame[
                frame["prior20_median_dollar_volume"].ge(250_000_000)
                & frame["volume_surprise"].ge(1.5)
                & frame[residual_column].abs().ge(0.01)
            ].copy()
            if leader_pool.empty:
                continue
            leader_pool["absolute_residual"] = leader_pool[
                residual_column
            ].abs()
            leader = leader_pool.sort_values(
                ["absolute_residual", "symbol"],
                ascending=[False, True],
            ).iloc[0]
            leader_residual = float(leader[residual_column])
            direction = 1 if leader_residual > 0 else -1
            peers = frame[frame["symbol"].ne(leader["symbol"])].copy()
            peers["signed_residual"] = direction * peers[residual_column]
            peers = peers[
                peers["signed_residual"].ge(-0.25 * abs(leader_residual))
                & peers["signed_residual"].le(0.50 * abs(leader_residual))
            ].sort_values(
                ["prior20_median_dollar_volume", "symbol"],
                ascending=[False, True],
            ).head(3)
            if peers.empty:
                continue
            last_accepted = bucket
            for peer in peers.itertuples(index=False):
                rows.append(
                    {
                        "event_id": (
                            f"{mode}|{pd.Timestamp(date).date()}|{bucket}|"
                            f"{leader['symbol']}"
                        ),
                        "mode": mode,
                        "date": pd.Timestamp(date),
                        "formation_start": bucket,
                        "entry_minute": int(peer.entry_minute),
                        "session_close_minute": int(
                            peer.session_close_minute
                        ),
                        "leader_symbol": str(leader["symbol"]),
                        "leader_residual": leader_residual,
                        "leader_volume_surprise": float(
                            leader["volume_surprise"]
                        ),
                        "symbol": str(peer.symbol),
                        "peer_residual": float(
                            getattr(peer, residual_column)
                        ),
                        "peer_signed_ratio": float(
                            peer.signed_residual / abs(leader_residual)
                        ),
                        "prior20_median_dollar_volume": float(
                            peer.prior20_median_dollar_volume
                        ),
                        "entry_raw": float(peer.entry_open_raw),
                        "trade_direction": (
                            "long" if direction > 0 else "short"
                        ),
                        "signal_sign": direction,
                    }
                )
    return pd.DataFrame(rows)


def add_exit_and_return(
    candidates: pd.DataFrame,
    minute_lookup: pd.DataFrame,
    horizon: str,
    cost: int,
) -> tuple[pd.DataFrame, dict]:
    rows: list[dict] = []
    attrition = {
        "input_peer_candidates": int(len(candidates)),
        "exit_after_close": 0,
        "missing_exit_bar": 0,
        "incomplete_path": 0,
        "executable_candidates": 0,
    }
    for candidate in candidates.itertuples(index=False):
        entry_minute = int(candidate.entry_minute)
        exit_minute = (
            int(candidate.session_close_minute) - 1
            if horizon == "close"
            else entry_minute + int(horizon)
        )
        if exit_minute >= int(candidate.session_close_minute):
            attrition["exit_after_close"] += 1
            continue
        key = (candidate.symbol, pd.Timestamp(candidate.date))
        if key not in minute_lookup.index:
            attrition["missing_exit_bar"] += 1
            continue
        path = minute_lookup.loc[key]
        if isinstance(path, pd.Series):
            path = path.to_frame().T
        path = path.set_index("minute_number")
        expected = list(range(entry_minute, exit_minute))
        if exit_minute not in path.index:
            attrition["missing_exit_bar"] += 1
            continue
        if not all(value in path.index for value in expected):
            attrition["incomplete_path"] += 1
            continue
        exit_price = float(path.loc[exit_minute, "open"])
        row = candidate._asdict()
        row["exit_minute"] = exit_minute
        row["exit_raw"] = exit_price
        row["entry_timestamp"] = clock(candidate.date, entry_minute)
        row["exit_timestamp"] = clock(candidate.date, exit_minute)
        if candidate.trade_direction == "short":
            unit_return, stopped, effective_exit = protected_short_return(
                float(candidate.entry_raw),
                exit_price,
                [float(value) for value in path.loc[expected, "high"]],
                0.02,
                cost,
                10,
            )
        else:
            unit_return = (
                exit_price / float(candidate.entry_raw)
                - 1
                - 2 * cost / 10_000
            )
            stopped = False
            effective_exit = exit_price
        row["unit_net_return"] = float(unit_return)
        row["stopped"] = bool(stopped)
        row["effective_exit_raw"] = float(effective_exit)
        rows.append(row)
    attrition["executable_candidates"] = len(rows)
    return pd.DataFrame(rows), attrition


def month_grid(daily: pd.DataFrame, start: pd.Timestamp) -> pd.Series:
    periods = pd.period_range(start=start, end=CUTOFF, freq="M")
    values = (
        daily[daily["date"].ge(start)]
        .assign(month=lambda frame: frame["date"].dt.to_period("M"))
        .groupby("month")["net_pnl"]
        .sum()
    )
    return values.reindex(periods, fill_value=0.0)


def metrics(
    variant_id: str,
    mode: str,
    direction: str,
    horizon: str,
    cost: int,
    trades: pd.DataFrame,
    daily: pd.DataFrame,
) -> dict:
    allocated = trades[trades["position_fraction"].gt(0)].copy()
    row = {
        "variant_id": variant_id,
        "mode": mode,
        "direction": direction,
        "horizon": horizon,
        "cost_bps_per_side": cost,
        "candidate_peers": int(len(trades)),
        "allocated_trades": int(len(allocated)),
        "events": int(allocated["event_id"].nunique()) if len(allocated) else 0,
        "leaders": int(allocated["leader_symbol"].nunique()) if len(allocated) else 0,
        "peers": int(allocated["symbol"].nunique()) if len(allocated) else 0,
        "total_net_return": float(daily["net_pnl"].sum()),
        "stop_count": int(allocated["stopped"].sum()) if len(allocated) else 0,
        "stop_rate": float(allocated["stopped"].mean()) if len(allocated) else 0.0,
    }
    drawdown, recovery, unresolved = max_drawdown_and_recovery(daily)
    row.update(
        {
            "maximum_drawdown": drawdown,
            "recovery_days": recovery,
            "recovery_unresolved": unresolved,
        }
    )
    for label, start in WINDOWS.items():
        months = month_grid(daily, start)
        row[f"{label}_average_month"] = float(months.mean())
        row[f"{label}_net_return"] = float(months.sum())
        row[f"{label}_positive_months"] = int(months.gt(0).sum())
        row[f"{label}_negative_months"] = int(months.lt(0).sum())
        row[f"{label}_inactive_months"] = int(months.eq(0).sum())
    for label, (start, end) in BLOCKS.items():
        row[f"{label}_net_return"] = float(
            daily.loc[daily["date"].between(start, end), "net_pnl"].sum()
        )
    if allocated.empty:
        return row
    positive_total = float(allocated["trade_pnl"].clip(lower=0).sum())
    by_day = allocated.groupby("date")["trade_pnl"].sum()
    by_leader = allocated.groupby("leader_symbol")["trade_pnl"].sum()
    by_peer = allocated.groupby("symbol")["trade_pnl"].sum()
    row.update(
        {
            "top5_event_positive_share": float(
                allocated.groupby("event_id")["trade_pnl"]
                .sum()
                .clip(lower=0)
                .nlargest(5)
                .sum()
                / positive_total
            )
            if positive_total > 0
            else np.nan,
            "top5_day_positive_share": float(
                by_day.clip(lower=0).nlargest(5).sum() / positive_total
            )
            if positive_total > 0
            else np.nan,
            "top_leader": str(by_leader.idxmax()),
            "top_leader_net_pnl": float(by_leader.max()),
            "top_peer": str(by_peer.idxmax()),
            "top_peer_net_pnl": float(by_peer.max()),
        }
    )
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--minute", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    record = yaml.safe_load(args.run_record.read_text(encoding="utf-8"))
    frozen = record["frozen_configuration"]
    if record["status"] != "frozen":
        raise RuntimeError("RUN-0001 record is not frozen")
    if frozen["expected_variant_count"]["total"] != 135:
        raise RuntimeError("Frozen variant count is not 135")

    features = pd.read_parquet(args.features)
    minutes = pd.read_parquet(args.minute)
    features["date"] = pd.to_datetime(features["date"])
    minutes["date"] = pd.to_datetime(minutes["date"])
    if max(features["date"].max(), minutes["date"].max()) > CUTOFF:
        raise RuntimeError("RUN-0001 input crosses sealed boundary")
    minute_lookup = minutes[
        ["symbol", "date", "minute_number", "open", "high", "low"]
    ].set_index(["symbol", "date"])
    sessions = pd.DatetimeIndex(sorted(minutes["date"].unique()))

    selected_by_mode = {
        mode: select_events(features, mode) for mode in MODES
    }
    metric_rows: list[dict] = []
    trade_frames: list[pd.DataFrame] = []
    daily_frames: list[pd.DataFrame] = []
    attrition: dict[str, dict] = {}
    for mode in MODES:
        selected = selected_by_mode[mode]
        for horizon in HORIZONS:
            for cost in COSTS:
                executable, path_attrition = add_exit_and_return(
                    selected, minute_lookup, horizon, cost
                )
                attrition[f"{mode}|{horizon}|{cost}bp"] = path_attrition
                for direction in DIRECTIONS:
                    if direction == "positive_only":
                        candidates = executable[
                            executable["signal_sign"].eq(1)
                        ].copy()
                    elif direction == "negative_only":
                        candidates = executable[
                            executable["signal_sign"].eq(-1)
                        ].copy()
                    else:
                        candidates = executable.copy()
                    variant_id = (
                        f"{mode}__{direction}__{horizon}__{cost}bp"
                    )
                    trades = allocate_intraday(candidates, 0.10, 0.10, 1.0)
                    trades["trade_pnl"] = (
                        trades["unit_net_return"]
                        * trades["position_fraction"]
                    )
                    trades["variant_id"] = variant_id
                    pnl = trades.groupby("date")["trade_pnl"].sum()
                    daily = pd.DataFrame({"date": sessions})
                    daily["net_pnl"] = daily["date"].map(pnl).fillna(0.0)
                    daily["variant_id"] = variant_id
                    metric_rows.append(
                        metrics(
                            variant_id,
                            mode,
                            direction,
                            horizon,
                            cost,
                            trades,
                            daily,
                        )
                    )
                    trade_frames.append(trades)
                    daily_frames.append(daily)

    metric_frame = pd.DataFrame(metric_rows)
    if (
        len(metric_frame) != 135
        or metric_frame["variant_id"].nunique() != 135
    ):
        raise RuntimeError(
            f"Executed {len(metric_frame)} variants, expected 135"
        )
    trades = pd.concat(trade_frames, ignore_index=True)
    daily = pd.concat(daily_frames, ignore_index=True)
    if not np.allclose(
        trades.groupby("variant_id")["trade_pnl"].sum().sort_index(),
        daily.groupby("variant_id")["net_pnl"].sum().sort_index(),
    ):
        raise RuntimeError("RUN-0001 aggregate P&L reconciliation failed")
    metric_frame = metric_frame.sort_values(
        ["recent_15m_average_month", "maximum_drawdown"],
        ascending=[False, True],
    ).reset_index(drop=True)
    metric_frame.to_parquet(
        args.output_dir / "variant_metrics.parquet", index=False
    )
    trades.to_parquet(args.output_dir / "trade_details.parquet", index=False)
    daily.to_parquet(args.output_dir / "daily_pnl.parquet", index=False)
    (args.output_dir / "attrition.json").write_text(
        json.dumps(
            {
                "selected_peer_candidates_by_mode": {
                    mode: int(len(frame))
                    for mode, frame in selected_by_mode.items()
                },
                "selected_events_by_mode": {
                    mode: int(frame["event_id"].nunique())
                    for mode, frame in selected_by_mode.items()
                },
                "path_attrition": attrition,
                "maximum_loaded_date": "2026-04-30",
                "holdout_rows_loaded": 0,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    frozen_hash = hashlib.sha256(
        json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    reconciliation = {
        "status": "passed",
        "run_id": "RUN-0001",
        "expected_variant_count": 135,
        "executed_variant_count": int(len(metric_frame)),
        "resolved_modes": list(MODES),
        "resolved_directions": list(DIRECTIONS),
        "resolved_horizons": list(HORIZONS),
        "resolved_costs": list(COSTS),
        "frozen_configuration_hash": frozen_hash,
        "input_hashes": {
            "features": sha256(args.features),
            "minute": sha256(args.minute),
        },
        "executed_code_hashes": {
            Path(__file__).name: sha256(Path(__file__)),
            "cam0009.py": sha256(Path(__file__).with_name("cam0009.py")),
        },
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2) + "\n", encoding="utf-8"
    )
    columns = [
        "variant_id",
        "mode",
        "direction",
        "horizon",
        "cost_bps_per_side",
        "candidate_peers",
        "allocated_trades",
        "events",
        "leaders",
        "peers",
        "recent_15m_average_month",
        "recent_12m_average_month",
        "full_average_month",
        "maximum_drawdown",
        "recovery_days",
        "recent_15m_positive_months",
        "recent_15m_negative_months",
        "recent_15m_inactive_months",
        "block_1_2024h2_net_return",
        "block_2_2025h1_net_return",
        "block_3_2025h2_net_return",
        "block_4_2026ytd_net_return",
        "stop_rate",
        "top5_event_positive_share",
        "top5_day_positive_share",
        "top_leader",
        "top_peer",
    ]
    top = metric_frame.head(30)[columns]
    (args.output_dir / "summary.json").write_text(
        json.dumps(
            {
                "status": "completed_uninterpreted",
                "variant_count": int(len(metric_frame)),
                "top_30": top.where(pd.notna(top), None).to_dict("records"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
