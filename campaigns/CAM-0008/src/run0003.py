from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from cam0008 import CUTOFF
from run0001 import classify, metrics, prepare_candidates, sha256, simulate
from run0002 import add_stock_state, load_qqq_state, screen_mask


PORTFOLIOS = (
    "positive_continuation_long",
    "negative_failure_long",
    "all_longs",
)
SCREEN_IDS = (
    "baseline",
    "stock_vol_high",
    "qqq_vol_high",
    "target_intraday",
    "gap_down1pct",
    "participation_le1pct",
    "reaction_ge50bp",
)
HORIZONS = ("five_close", "ten_close")
COSTS = (10, 20)
POSITION_CAPS = (0.02, 1 / 30, 0.05, 0.10)


def exposure_statistics(
    trades: pd.DataFrame, sessions: pd.DatetimeIndex
) -> dict[str, float]:
    allocated = trades[trades["position_fraction"].gt(0)].copy()
    if allocated.empty:
        return {
            "maximum_actual_gross": 0.0,
            "maximum_actual_symbol_gross": 0.0,
            "average_close_gross": 0.0,
            "median_close_gross": 0.0,
            "fraction_close_gross_ge_99pct": 0.0,
        }
    events = pd.concat(
        [
            allocated[
                [
                    "symbol",
                    "entry_timestamp",
                    "position_fraction",
                ]
            ]
            .rename(columns={"entry_timestamp": "timestamp"})
            .assign(change=lambda frame: frame["position_fraction"]),
            allocated[
                [
                    "symbol",
                    "exit_timestamp",
                    "position_fraction",
                ]
            ]
            .rename(columns={"exit_timestamp": "timestamp"})
            .assign(change=lambda frame: -frame["position_fraction"]),
        ],
        ignore_index=True,
    )
    gross = (
        events.groupby("timestamp")["change"].sum().sort_index().cumsum()
    )
    symbol = (
        events.groupby(["symbol", "timestamp"])["change"]
        .sum()
        .groupby(level=0)
        .cumsum()
    )
    changes = events.groupby("timestamp")["change"].sum().sort_index()
    close_times = pd.DatetimeIndex(sessions) + pd.Timedelta(hours=16)
    close_gross = (
        changes.reindex(changes.index.union(close_times), fill_value=0.0)
        .sort_index()
        .cumsum()
        .reindex(close_times)
        .to_numpy()
    )
    return {
        "maximum_actual_gross": float(gross.max()),
        "maximum_actual_symbol_gross": float(symbol.max()),
        "average_close_gross": float(close_gross.mean()),
        "median_close_gross": float(np.median(close_gross)),
        "fraction_close_gross_ge_99pct": float(
            np.mean(close_gross >= 0.99)
        ),
    }


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
        raise RuntimeError("RUN-0003 record is not frozen")
    if tuple(frozen["portfolios"]) != PORTFOLIOS:
        raise RuntimeError("Portfolio list differs from frozen record")
    if tuple(frozen["screen_ids"]) != SCREEN_IDS:
        raise RuntimeError("Screen list differs from frozen record")
    if not np.allclose(frozen["position_caps"], POSITION_CAPS):
        raise RuntimeError("Position caps differ from frozen record")
    if frozen["expected_variant_count"]["total"] != 336:
        raise RuntimeError("Frozen variant count is not 336")

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
        raise RuntimeError("RUN-0003 input crosses sealed boundary")

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
                    candidates = prepare_candidates(
                        screened, portfolio, horizon, cost
                    )
                    for cap in POSITION_CAPS:
                        cap_label = f"{cap:.6f}".rstrip("0").rstrip(".")
                        variant_id = (
                            f"{portfolio}__{screen_id}__{horizon}"
                            f"__{cost}bp__cap{cap_label}"
                        )
                        trades, daily_pnl = simulate(
                            candidates,
                            daily_prices,
                            sessions,
                            cost,
                            cap,
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
                        row["position_cap"] = cap
                        row.update(exposure_statistics(trades, sessions))
                        metric_rows.append(row)
                        trade_frames.append(trades)
                        daily_frames.append(daily_pnl)

    metric_frame = pd.DataFrame(metric_rows)
    if (
        len(metric_frame) != 336
        or metric_frame["variant_id"].nunique() != 336
    ):
        raise RuntimeError(
            f"Executed {len(metric_frame)} variants, expected 336"
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
        raise RuntimeError("RUN-0003 aggregate P&L reconciliation failed")
    if metric_frame["maximum_actual_gross"].max() > 1.0 + 1e-10:
        raise RuntimeError("Maximum gross exposure exceeded")
    if metric_frame["maximum_actual_symbol_gross"].max() > 0.10 + 1e-10:
        raise RuntimeError("Maximum symbol exposure exceeded")

    metric_frame = metric_frame.sort_values(
        ["recent_15m_average_month", "maximum_drawdown"],
        ascending=[False, True],
    ).reset_index(drop=True)
    metric_frame.to_parquet(
        args.output_dir / "variant_metrics.parquet", index=False
    )
    trades.to_parquet(args.output_dir / "trade_details.parquet", index=False)
    daily_pnl.to_parquet(args.output_dir / "daily_pnl.parquet", index=False)
    (args.output_dir / "attrition.json").write_text(
        json.dumps(
            {
                "readiness_events": int(len(readiness)),
                "base_eligible_long_events": int(len(eligible)),
                "screen_candidate_counts": screen_counts,
                "maximum_loaded_date": "2026-04-30",
                "holdout_rows_loaded": 0,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    qqq_hash = hashlib.sha256(
        qqq.to_csv(index=False).encode()
    ).hexdigest()
    frozen_hash = hashlib.sha256(
        json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    reconciliation = {
        "status": "passed",
        "run_id": "RUN-0003",
        "expected_variant_count": 336,
        "executed_variant_count": int(len(metric_frame)),
        "resolved_portfolios": list(PORTFOLIOS),
        "resolved_screens": list(SCREEN_IDS),
        "resolved_horizons": list(HORIZONS),
        "resolved_costs": list(COSTS),
        "resolved_position_caps": list(POSITION_CAPS),
        "frozen_configuration_hash": frozen_hash,
        "input_hashes": {
            "event_readiness": sha256(args.event_readiness),
            "daily_split": sha256(args.daily_split),
            "qqq_query_output": qqq_hash,
        },
        "executed_code_hashes": {
            Path(__file__).name: sha256(Path(__file__)),
            "run0002.py": sha256(Path(__file__).with_name("run0002.py")),
            "run0001.py": sha256(Path(__file__).with_name("run0001.py")),
            "cam0008.py": sha256(Path(__file__).with_name("cam0008.py")),
        },
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
        "position_cap",
        "candidate_events",
        "allocated_trades",
        "symbols",
        "average_close_gross",
        "maximum_actual_gross",
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
    top = metric_frame.head(50)[summary_columns]
    (args.output_dir / "summary.json").write_text(
        json.dumps(
            {
                "status": "completed_uninterpreted",
                "variant_count": int(len(metric_frame)),
                "top_50": top.where(pd.notna(top), None).to_dict("records"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
