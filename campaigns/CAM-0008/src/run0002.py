from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml

from cam0008 import CUTOFF
from run0001 import (
    classify,
    metrics,
    prepare_candidates,
    sha256,
    simulate,
)


PORTFOLIOS = (
    "positive_continuation_long",
    "negative_failure_long",
    "all_longs",
)
SCREEN_IDS = (
    "baseline",
    "standalone",
    "target_only",
    "rating_only",
    "initiation_only",
    "target_raise",
    "target_lower",
    "rating_upgrade",
    "rating_downgrade",
    "premarket",
    "intraday",
    "after_close",
    "reaction_le25bp",
    "reaction_ge10bp",
    "reaction_ge25bp",
    "reaction_ge50bp",
    "reaction_ge100bp",
    "close_location67",
    "close_location80",
    "participation_le1pct",
    "participation_le2pct",
    "participation_ge5pct",
    "participation_ge10pct",
    "gap_down1pct",
    "gap_down2pct",
    "gap_up1pct",
    "gap_abs2pct",
    "single_firm",
    "multifirm2",
    "multifirm3",
    "stock_vol_low",
    "stock_vol_high",
    "qqq_trend_down",
    "qqq_trend_up",
    "qqq_vol_low",
    "qqq_vol_high",
    "standalone_target",
    "standalone_intraday",
    "target_intraday",
    "standalone_participation_le2pct",
)
HORIZONS = ("three_close", "five_close", "ten_close")
COSTS = (10, 20)


def add_stock_state(daily: pd.DataFrame) -> pd.DataFrame:
    frame = daily.sort_values(["symbol", "date"]).copy()
    frame["return"] = frame.groupby("symbol")["close"].pct_change(
        fill_method=None
    )
    frame["stock_vol20"] = frame.groupby("symbol")["return"].transform(
        lambda values: values.shift(1).rolling(20, min_periods=20).std()
        * np.sqrt(252)
    )
    frame["stock_vol_prior60_median"] = frame.groupby("symbol")[
        "stock_vol20"
    ].transform(
        lambda values: values.shift(1).rolling(60, min_periods=40).median()
    )
    frame["stock_vol_high"] = frame["stock_vol20"].ge(
        frame["stock_vol_prior60_median"]
    )
    return frame


def load_qqq_state(catalog: Path) -> pd.DataFrame:
    connection = duckdb.connect(str(catalog), read_only=True)
    try:
        frame = connection.execute(
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
        connection.close()
    frame["date"] = pd.to_datetime(frame["date"])
    if (
        frame.empty
        or frame["date"].max() > CUTOFF
        or frame.duplicated("date").any()
    ):
        raise RuntimeError("Invalid QQQ state input")
    frame["return"] = frame["close"].pct_change(fill_method=None)
    frame["qqq_prior20_return"] = (
        frame["close"].shift(1) / frame["close"].shift(21) - 1
    )
    frame["qqq_vol20"] = (
        frame["return"].shift(1).rolling(20, min_periods=20).std()
        * np.sqrt(252)
    )
    frame["qqq_vol_prior60_median"] = (
        frame["qqq_vol20"].shift(1).rolling(60, min_periods=40).median()
    )
    frame["qqq_vol_high"] = frame["qqq_vol20"].ge(
        frame["qqq_vol_prior60_median"]
    )
    return frame


def screen_mask(frame: pd.DataFrame, screen_id: str) -> pd.Series:
    action = frame["primary_action_type"]
    if screen_id == "baseline":
        return pd.Series(True, index=frame.index)
    if screen_id == "standalone":
        return ~frame["within_36h_after_earnings"]
    if screen_id == "target_only":
        return action.isin(["target_raise", "target_lower"])
    if screen_id == "rating_only":
        return action.isin(["rating_upgrade", "rating_downgrade"])
    if screen_id == "initiation_only":
        return action.isin(["positive_initiation", "negative_initiation"])
    if screen_id in {
        "target_raise",
        "target_lower",
        "rating_upgrade",
        "rating_downgrade",
    }:
        return action.eq(screen_id)
    if screen_id in {"premarket", "intraday", "after_close"}:
        return frame["release_bucket"].eq(screen_id)
    if screen_id == "reaction_le25bp":
        return frame["reaction_return"].le(0.0025)
    if screen_id.startswith("reaction_ge"):
        thresholds = {
            "reaction_ge10bp": 0.0010,
            "reaction_ge25bp": 0.0025,
            "reaction_ge50bp": 0.0050,
            "reaction_ge100bp": 0.0100,
        }
        return frame["reaction_return"].ge(thresholds[screen_id])
    if screen_id == "close_location67":
        return frame["reaction_close_location"].ge(0.67)
    if screen_id == "close_location80":
        return frame["reaction_close_location"].ge(0.80)
    if screen_id == "participation_le1pct":
        return frame["reaction_dollar_participation"].le(0.01)
    if screen_id == "participation_le2pct":
        return frame["reaction_dollar_participation"].le(0.02)
    if screen_id == "participation_ge5pct":
        return frame["reaction_dollar_participation"].ge(0.05)
    if screen_id == "participation_ge10pct":
        return frame["reaction_dollar_participation"].ge(0.10)
    if screen_id == "gap_down1pct":
        return frame["gap_return"].le(-0.01)
    if screen_id == "gap_down2pct":
        return frame["gap_return"].le(-0.02)
    if screen_id == "gap_up1pct":
        return frame["gap_return"].ge(0.01)
    if screen_id == "gap_abs2pct":
        return frame["gap_return"].abs().ge(0.02)
    if screen_id == "single_firm":
        return frame["firm_count"].eq(1)
    if screen_id == "multifirm2":
        return frame["firm_count"].ge(2)
    if screen_id == "multifirm3":
        return frame["firm_count"].ge(3)
    if screen_id == "stock_vol_low":
        return frame["stock_vol20"].notna() & frame["stock_vol_high"].eq(False)
    if screen_id == "stock_vol_high":
        return frame["stock_vol20"].notna() & frame["stock_vol_high"]
    if screen_id == "qqq_trend_down":
        return frame["qqq_prior20_return"].le(0)
    if screen_id == "qqq_trend_up":
        return frame["qqq_prior20_return"].gt(0)
    if screen_id == "qqq_vol_low":
        return frame["qqq_vol20"].notna() & frame["qqq_vol_high"].eq(False)
    if screen_id == "qqq_vol_high":
        return frame["qqq_vol20"].notna() & frame["qqq_vol_high"]
    if screen_id == "standalone_target":
        return (
            ~frame["within_36h_after_earnings"]
            & action.isin(["target_raise", "target_lower"])
        )
    if screen_id == "standalone_intraday":
        return (
            ~frame["within_36h_after_earnings"]
            & frame["release_bucket"].eq("intraday")
        )
    if screen_id == "target_intraday":
        return (
            action.isin(["target_raise", "target_lower"])
            & frame["release_bucket"].eq("intraday")
        )
    if screen_id == "standalone_participation_le2pct":
        return (
            ~frame["within_36h_after_earnings"]
            & frame["reaction_dollar_participation"].le(0.02)
        )
    raise KeyError(screen_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--event-readiness", type=Path, required=True)
    parser.add_argument("--daily-split", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    record = yaml.safe_load(args.run_record.read_text(encoding="utf-8"))
    frozen = record["frozen_configuration"]
    if record["status"] != "frozen":
        raise RuntimeError("RUN-0002 record is not frozen")
    if tuple(frozen["portfolios"]) != PORTFOLIOS:
        raise RuntimeError("Portfolio list differs from frozen record")
    if tuple(frozen["screen_ids"]) != SCREEN_IDS:
        raise RuntimeError("Screen list differs from frozen record")
    if frozen["expected_variant_count"]["total"] != 720:
        raise RuntimeError("Frozen variant count is not 720")

    readiness = pd.read_parquet(args.event_readiness)
    daily_prices = pd.read_parquet(args.daily_split)
    readiness["entry_session"] = pd.to_datetime(readiness["entry_session"])
    daily_prices["date"] = pd.to_datetime(daily_prices["date"])
    qqq = load_qqq_state(args.catalog)
    if max(
        readiness["entry_session"].max(),
        daily_prices["date"].max(),
        qqq["date"].max(),
    ) > CUTOFF:
        raise RuntimeError("RUN-0002 input crosses sealed boundary")

    stock_state = add_stock_state(daily_prices)
    readiness = readiness.merge(
        stock_state[
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
        validate="many_to_one",
    ).drop(columns="date")
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
    ).drop(columns="date")
    readiness["leg"] = readiness.apply(classify, axis=1)
    eligible = readiness[
        readiness["signal_complete"]
        & readiness["prior20_median_dollar_volume"].ge(100_000_000)
        & readiness["leg"].isin(
            ["positive_continuation_long", "negative_failure_long"]
        )
    ].copy()
    sessions = pd.DatetimeIndex(
        sorted(daily_prices["date"].drop_duplicates())
    )

    metric_rows: list[dict] = []
    trade_frames: list[pd.DataFrame] = []
    daily_frames: list[pd.DataFrame] = []
    screen_counts: dict[str, int] = {}
    for portfolio in PORTFOLIOS:
        portfolio_frame = (
            eligible[eligible["leg"].eq(portfolio)]
            if portfolio != "all_longs"
            else eligible
        )
        for screen_id in SCREEN_IDS:
            screened = portfolio_frame[screen_mask(portfolio_frame, screen_id)]
            screen_counts[f"{portfolio}|{screen_id}"] = int(len(screened))
            for horizon in HORIZONS:
                for cost in COSTS:
                    variant_id = (
                        f"{portfolio}__{screen_id}__{horizon}__{cost}bp"
                    )
                    candidates = prepare_candidates(
                        screened, portfolio, horizon, cost
                    )
                    trades, daily_pnl = simulate(
                        candidates,
                        daily_prices,
                        sessions,
                        cost,
                        0.02,
                    )
                    trades["variant_id"] = variant_id
                    daily_pnl["variant_id"] = variant_id
                    row = metrics(
                        variant_id,
                        "all_events",
                        portfolio,
                        horizon,
                        cost,
                        trades,
                        daily_pnl,
                    )
                    row["screen_id"] = screen_id
                    metric_rows.append(row)
                    trade_frames.append(trades)
                    daily_frames.append(daily_pnl)

    metric_frame = pd.DataFrame(metric_rows)
    if (
        len(metric_frame) != 720
        or metric_frame["variant_id"].nunique() != 720
    ):
        raise RuntimeError(
            f"Executed {len(metric_frame)} variants, expected 720"
        )
    trades = pd.concat(trade_frames, ignore_index=True)
    daily_pnl = pd.concat(daily_frames, ignore_index=True)
    daily_totals = daily_pnl.groupby("variant_id")["net_pnl"].sum().sort_index()
    trade_totals = (
        trades.groupby("variant_id")["trade_pnl"]
        .sum()
        .reindex(daily_totals.index, fill_value=0.0)
    )
    if not np.allclose(trade_totals, daily_totals):
        raise RuntimeError("RUN-0002 aggregate P&L reconciliation failed")

    metric_frame = metric_frame.sort_values(
        ["recent_15m_average_month", "maximum_drawdown"],
        ascending=[False, True],
    ).reset_index(drop=True)
    metric_frame.to_parquet(
        args.output_dir / "variant_metrics.parquet", index=False
    )
    trades.to_parquet(args.output_dir / "trade_details.parquet", index=False)
    daily_pnl.to_parquet(args.output_dir / "daily_pnl.parquet", index=False)
    attrition = {
        "readiness_events": int(len(readiness)),
        "base_eligible_long_events": int(len(eligible)),
        "missing_stock_state": int(eligible["stock_vol20"].isna().sum()),
        "missing_qqq_trend_state": int(
            eligible["qqq_prior20_return"].isna().sum()
        ),
        "missing_qqq_vol_state": int(eligible["qqq_vol20"].isna().sum()),
        "screen_candidate_counts": screen_counts,
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "attrition.json").write_text(
        json.dumps(attrition, indent=2) + "\n", encoding="utf-8"
    )
    frozen_hash = hashlib.sha256(
        json.dumps(
            frozen, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    reconciliation = {
        "status": "passed",
        "run_id": "RUN-0002",
        "expected_variant_count": 720,
        "executed_variant_count": int(len(metric_frame)),
        "resolved_portfolios": list(PORTFOLIOS),
        "resolved_screens": list(SCREEN_IDS),
        "resolved_horizons": list(HORIZONS),
        "resolved_costs": list(COSTS),
        "frozen_configuration_hash": frozen_hash,
        "input_hashes": {
            "event_readiness": sha256(args.event_readiness),
            "daily_split": sha256(args.daily_split),
            "catalog_file": sha256(args.catalog),
        },
        "executed_code_hashes": {
            Path(__file__).name: sha256(Path(__file__)),
            "run0001.py": sha256(Path(__file__).with_name("run0001.py")),
            "cam0008.py": sha256(Path(__file__).with_name("cam0008.py")),
        },
        "qqq_rows": int(len(qqq)),
        "qqq_minimum_date": str(qqq["date"].min().date()),
        "qqq_maximum_date": str(qqq["date"].max().date()),
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2) + "\n", encoding="utf-8"
    )
    summary_columns = [
        "variant_id",
        "selector",
        "screen_id",
        "horizon",
        "cost_bps_per_side",
        "candidate_events",
        "allocated_trades",
        "symbols",
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
        "top5_event_positive_share",
        "top5_day_positive_share",
        "top_symbol",
        "top_firm",
    ]
    (args.output_dir / "summary.json").write_text(
        json.dumps(
            {
                "status": "completed_uninterpreted",
                "variant_count": int(len(metric_frame)),
                "top_40": metric_frame.head(40)[summary_columns].where(
                    pd.notna(metric_frame.head(40)[summary_columns]), None
                ).to_dict("records"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
