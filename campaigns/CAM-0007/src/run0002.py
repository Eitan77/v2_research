from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml

from cam0007 import CUTOFF
from run0001 import build_metrics, multiday_variant


COSTS = (5, 10, 20)
HORIZONS = ("three_close", "five_close", "ten_close")
LEGS = ("long_positive_continuation", "long_negative_failure")
SCREEN_IDS = (
    "base_gap2",
    "gap1",
    "gap1_5",
    "gap3",
    "gap5",
    "gap8",
    "reaction50bp",
    "reaction100bp",
    "reaction200bp",
    "participation10",
    "participation20",
    "participation30",
    "close_location67",
    "close_location80",
    "after_close",
    "premarket",
    "qqq_up",
    "qqq_down",
    "stock_vol_high",
    "stock_vol_low",
    "gap3_reaction50bp",
    "reaction100bp_close67",
    "participation20_close67",
    "gap3_reaction50bp_participation10",
    "after_close_gap3_reaction50bp",
    "reaction_gap_ratio25",
    "reaction_gap_ratio50",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify_long(row: pd.Series) -> str | None:
    if float(row["first30_return"]) <= 0:
        return None
    if float(row["gap_return"]) > 0:
        return "long_positive_continuation"
    if float(row["gap_return"]) < 0:
        return "long_negative_failure"
    return None


def add_prior_states(daily: pd.DataFrame) -> pd.DataFrame:
    frame = daily.sort_values(["symbol", "date"]).copy()
    frame["daily_return"] = frame.groupby("symbol")["close"].pct_change(
        fill_method=None
    )
    frame["stock_vol20"] = frame.groupby("symbol")["daily_return"].transform(
        lambda x: x.shift(1).rolling(20, min_periods=20).std()
        * np.sqrt(252)
    )
    frame["stock_vol_prior60_median"] = frame.groupby("symbol")[
        "stock_vol20"
    ].transform(lambda x: x.shift(1).rolling(60, min_periods=40).median())
    frame["stock_vol_high"] = frame["stock_vol20"].ge(
        frame["stock_vol_prior60_median"]
    )
    return frame


def screen_mask(frame: pd.DataFrame, screen_id: str) -> pd.Series:
    gap = frame["gap_return"].abs()
    base = gap.ge(0.02)
    if screen_id == "base_gap2":
        return base
    if screen_id == "gap1":
        return gap.ge(0.01)
    if screen_id == "gap1_5":
        return gap.ge(0.015)
    if screen_id == "gap3":
        return gap.ge(0.03)
    if screen_id == "gap5":
        return gap.ge(0.05)
    if screen_id == "gap8":
        return gap.ge(0.08)
    if screen_id == "reaction50bp":
        return base & frame["first30_return"].ge(0.005)
    if screen_id == "reaction100bp":
        return base & frame["first30_return"].ge(0.01)
    if screen_id == "reaction200bp":
        return base & frame["first30_return"].ge(0.02)
    if screen_id == "participation10":
        return base & frame["first30_dollar_participation"].ge(0.10)
    if screen_id == "participation20":
        return base & frame["first30_dollar_participation"].ge(0.20)
    if screen_id == "participation30":
        return base & frame["first30_dollar_participation"].ge(0.30)
    if screen_id == "close_location67":
        return base & frame["first30_close_location"].ge(0.67)
    if screen_id == "close_location80":
        return base & frame["first30_close_location"].ge(0.80)
    if screen_id == "after_close":
        return base & frame["announcement_bucket"].eq("after_close")
    if screen_id == "premarket":
        return base & frame["announcement_bucket"].eq("premarket")
    if screen_id == "qqq_up":
        return base & frame["qqq_prior20_return"].gt(0)
    if screen_id == "qqq_down":
        return base & frame["qqq_prior20_return"].le(0)
    if screen_id == "stock_vol_high":
        return base & frame["stock_vol_high"].eq(True)
    if screen_id == "stock_vol_low":
        return base & frame["stock_vol_high"].eq(False)
    if screen_id == "gap3_reaction50bp":
        return gap.ge(0.03) & frame["first30_return"].ge(0.005)
    if screen_id == "reaction100bp_close67":
        return (
            base
            & frame["first30_return"].ge(0.01)
            & frame["first30_close_location"].ge(0.67)
        )
    if screen_id == "participation20_close67":
        return (
            base
            & frame["first30_dollar_participation"].ge(0.20)
            & frame["first30_close_location"].ge(0.67)
        )
    if screen_id == "gap3_reaction50bp_participation10":
        return (
            gap.ge(0.03)
            & frame["first30_return"].ge(0.005)
            & frame["first30_dollar_participation"].ge(0.10)
        )
    if screen_id == "after_close_gap3_reaction50bp":
        return (
            frame["announcement_bucket"].eq("after_close")
            & gap.ge(0.03)
            & frame["first30_return"].ge(0.005)
        )
    if screen_id == "reaction_gap_ratio25":
        return base & frame["reaction_gap_ratio"].ge(0.25)
    if screen_id == "reaction_gap_ratio50":
        return base & frame["reaction_gap_ratio"].ge(0.50)
    raise KeyError(screen_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--event-readiness", type=Path, required=True)
    parser.add_argument("--event-minutes", type=Path, required=True)
    parser.add_argument("--daily-split", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    record = yaml.safe_load(args.run_record.read_text(encoding="utf-8"))
    if record["status"] != "frozen":
        raise RuntimeError("RUN-0002 record is not frozen")
    if record["frozen_configuration"]["expected_variant_count"]["total"] != 522:
        raise RuntimeError("Frozen variant count is not 522")
    if tuple(record["frozen_configuration"]["screen_ids"]) != SCREEN_IDS:
        raise RuntimeError("Frozen screen list differs from executable")

    readiness = pd.read_parquet(args.event_readiness)
    minutes = pd.read_parquet(args.event_minutes)
    daily = pd.read_parquet(args.daily_split)
    readiness["entry_session"] = pd.to_datetime(readiness["entry_session"])
    minutes["date"] = pd.to_datetime(minutes["date"])
    daily["date"] = pd.to_datetime(daily["date"])
    if max(
        readiness["entry_session"].max(),
        minutes["date"].max(),
        daily["date"].max(),
    ) > CUTOFF:
        raise RuntimeError("RUN-0002 local input crosses sealed boundary")

    opening = minutes[minutes["minute"].between("09:30", "09:59")].copy()
    opening_features = (
        opening.groupby(["symbol", "date"])
        .agg(first30_high=("high", "max"), first30_low=("low", "min"))
        .reset_index()
    )
    readiness = readiness.merge(
        opening_features,
        left_on=["symbol", "entry_session"],
        right_on=["symbol", "date"],
        how="left",
        validate="one_to_one",
    ).drop(columns=["date"])
    first_range = readiness["first30_high"] - readiness["first30_low"]
    readiness["first30_close_location"] = np.where(
        first_range.gt(0),
        (readiness["close_0959_raw"] - readiness["first30_low"]) / first_range,
        np.nan,
    )
    readiness["reaction_gap_ratio"] = (
        readiness["first30_return"] / readiness["gap_return"].abs()
    )

    state = add_prior_states(daily)
    readiness = readiness.merge(
        state[
            [
                "symbol",
                "date",
                "stock_vol20",
                "stock_vol_prior60_median",
                "stock_vol_high",
            ]
        ],
        left_on=["symbol", "entry_session"],
        right_on=["symbol", "date"],
        how="left",
        validate="one_to_one",
    ).drop(columns=["date"])

    con = duckdb.connect(str(args.catalog), read_only=True)
    try:
        qqq = con.execute(
            """
            SELECT date, close
            FROM bars_1d
            WHERE symbol='QQQ' AND feed='sip' AND adjustment='split'
              AND timeframe='1Day'
              AND date BETWEEN DATE '2024-05-01' AND DATE '2026-04-30'
            ORDER BY date
            """
        ).fetch_df()
    finally:
        con.close()
    qqq["date"] = pd.to_datetime(qqq["date"])
    if qqq["date"].max() > CUTOFF or qqq.duplicated("date").any():
        raise RuntimeError("Invalid QQQ state input")
    qqq["return"] = qqq["close"].pct_change(fill_method=None)
    qqq["qqq_prior20_return"] = qqq["close"].shift(1) / qqq["close"].shift(21) - 1
    qqq["qqq_vol20"] = (
        qqq["return"].shift(1).rolling(20, min_periods=20).std() * np.sqrt(252)
    )
    qqq["qqq_vol_prior60_median"] = (
        qqq["qqq_vol20"].shift(1).rolling(60, min_periods=40).median()
    )
    qqq["qqq_vol_high"] = qqq["qqq_vol20"].ge(
        qqq["qqq_vol_prior60_median"]
    )
    readiness = readiness.merge(
        qqq[
            [
                "date",
                "qqq_prior20_return",
                "qqq_vol20",
                "qqq_vol_prior60_median",
                "qqq_vol_high",
            ]
        ],
        left_on="entry_session",
        right_on="date",
        how="left",
        validate="many_to_one",
    ).drop(columns=["date"])
    readiness["leg"] = readiness.apply(classify_long, axis=1)
    base_eligible = readiness[
        readiness["signal_complete"]
        & readiness["prior20_median_dollar_volume"].ge(100_000_000)
        & readiness["leg"].isin(LEGS)
    ].copy()
    sessions = pd.DatetimeIndex(sorted(daily["date"].drop_duplicates()))
    session_number = {pd.Timestamp(date): index for index, date in enumerate(sessions)}

    metric_rows = []
    trade_frames = []
    daily_frames = []
    screen_counts = {}
    for leg in LEGS:
        leg_frame = base_eligible[base_eligible["leg"].eq(leg)]
        for screen_id in SCREEN_IDS:
            screened = leg_frame[screen_mask(leg_frame, screen_id)].copy()
            screen_counts[f"{leg}|{screen_id}"] = int(len(screened))
            for horizon in HORIZONS:
                for cost in COSTS:
                    variant_id = (
                        f"{leg}__{screen_id}__{horizon}__{cost}bp__cap20"
                    )
                    trades, daily_pnl = multiday_variant(
                        screened,
                        daily,
                        screen_id,
                        {leg},
                        horizon,
                        cost,
                        sessions,
                        session_number,
                        0.20,
                    )
                    trades["variant_id"] = variant_id
                    daily_pnl["variant_id"] = variant_id
                    metrics = build_metrics(
                        variant_id, screen_id, horizon, cost, trades, daily_pnl
                    )
                    metrics.update(
                        {
                            "leg": leg,
                            "screen_id": screen_id,
                            "position_cap": 0.20,
                        }
                    )
                    metric_rows.append(metrics)
                    trade_frames.append(trades)
                    daily_frames.append(daily_pnl)
        screened = leg_frame[screen_mask(leg_frame, "base_gap2")].copy()
        for position_cap in (0.10, 0.33):
            for horizon in HORIZONS:
                for cost in COSTS:
                    cap_label = int(round(position_cap * 100))
                    variant_id = (
                        f"{leg}__base_gap2__{horizon}__{cost}bp__cap{cap_label}"
                    )
                    trades, daily_pnl = multiday_variant(
                        screened,
                        daily,
                        "base_gap2",
                        {leg},
                        horizon,
                        cost,
                        sessions,
                        session_number,
                        position_cap,
                    )
                    trades["variant_id"] = variant_id
                    daily_pnl["variant_id"] = variant_id
                    metrics = build_metrics(
                        variant_id, "base_gap2", horizon, cost, trades, daily_pnl
                    )
                    metrics.update(
                        {
                            "leg": leg,
                            "screen_id": "base_gap2",
                            "position_cap": position_cap,
                        }
                    )
                    metric_rows.append(metrics)
                    trade_frames.append(trades)
                    daily_frames.append(daily_pnl)
    metrics = pd.DataFrame(metric_rows)
    if len(metrics) != 522 or metrics["variant_id"].nunique() != 522:
        raise RuntimeError(f"Executed {len(metrics)} variants, expected 522")
    if metrics["maximum_gross_exposure"].max() > 1.00000001:
        raise RuntimeError("Gross exposure cap breached")
    trades = pd.concat(trade_frames, ignore_index=True)
    daily_pnl = pd.concat(daily_frames, ignore_index=True)
    trade_sum = trades.groupby("variant_id")["trade_pnl"].sum().sort_index()
    daily_sum = daily_pnl.groupby("variant_id")["net_pnl"].sum().sort_index()
    if not np.allclose(trade_sum, daily_sum):
        raise RuntimeError("RUN-0002 trade/daily reconciliation failed")

    metrics = metrics.sort_values(
        ["recent_15m_average_month", "maximum_drawdown"],
        ascending=[False, True],
    ).reset_index(drop=True)
    metrics.to_parquet(args.output_dir / "variant_metrics.parquet", index=False)
    trades.to_parquet(args.output_dir / "trade_details.parquet", index=False)
    daily_pnl.to_parquet(args.output_dir / "daily_pnl.parquet", index=False)
    feature_columns = [
        "symbol",
        "event_timestamp",
        "entry_session",
        "leg",
        "gap_return",
        "first30_return",
        "first30_close_location",
        "first30_dollar_participation",
        "reaction_gap_ratio",
        "stock_vol20",
        "stock_vol_prior60_median",
        "stock_vol_high",
        "qqq_prior20_return",
        "qqq_vol20",
        "qqq_vol_prior60_median",
        "qqq_vol_high",
    ]
    base_eligible[feature_columns].to_parquet(
        args.output_dir / "causal_features.parquet", index=False
    )
    attrition = {
        "registry_events": int(len(readiness)),
        "long_reaction_eligible_before_screens": int(len(base_eligible)),
        "leg_counts_before_screens": {
            str(key): int(value)
            for key, value in base_eligible["leg"].value_counts().items()
        },
        "feature_nonmissing": {
            column: int(base_eligible[column].notna().sum())
            for column in feature_columns[4:]
        },
        "screen_counts": screen_counts,
        "maximum_loaded_date": str(
            max(
                readiness["entry_session"].max(),
                daily["date"].max(),
                qqq["date"].max(),
            ).date()
        ),
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "attrition.json").write_text(
        json.dumps(attrition, indent=2) + "\n", encoding="utf-8"
    )
    frozen_hash = hashlib.sha256(
        json.dumps(
            record["frozen_configuration"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    code_paths = [Path(__file__), Path(__file__).with_name("run0001.py"), Path(__file__).with_name("cam0007.py")]
    reconciliation = {
        "status": "passed",
        "run_id": "RUN-0002",
        "expected_variant_count": 522,
        "executed_variant_count": int(len(metrics)),
        "resolved_screen_ids": list(SCREEN_IDS),
        "resolved_legs": list(LEGS),
        "resolved_horizons": list(HORIZONS),
        "resolved_costs_bps_per_side": list(COSTS),
        "resolved_position_caps": [0.10, 0.20, 0.33],
        "frozen_configuration_hash": frozen_hash,
        "input_hashes": {
            "event_readiness": sha256(args.event_readiness),
            "event_minutes": sha256(args.event_minutes),
            "daily_split": sha256(args.daily_split),
        },
        "executed_code_hashes": {path.name: sha256(path) for path in code_paths},
        "maximum_loaded_date": attrition["maximum_loaded_date"],
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2) + "\n", encoding="utf-8"
    )
    primary = metrics[
        metrics["cost_bps_per_side"].eq(10)
        & metrics["position_cap"].eq(0.20)
    ]
    summary_columns = [
        "variant_id",
        "leg",
        "screen_id",
        "horizon",
        "allocated_trades",
        "symbols",
        "recent_15m_average_month",
        "recent_12m_average_month",
        "full_average_month",
        "recent_15m_positive_months",
        "recent_15m_negative_months",
        "recent_15m_inactive_months",
        "maximum_drawdown",
        "recovery_days",
        "top5_event_positive_share",
        "top_symbol",
    ]
    summary = {
        "status": "completed_uninterpreted",
        "variant_count": int(len(metrics)),
        "primary_sort": "10 bp per side, 20% cap, recent 15-month average",
        "top_20_primary": primary[summary_columns]
        .head(20)
        .replace({np.nan: None})
        .to_dict(orient="records"),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
