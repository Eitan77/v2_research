from __future__ import annotations

import math

import pandas as pd

from ar_pipeline.engines.portfolio_validation import PRIMARY_VARIANT, RAW_VARIANT, validate_trade_ledger


def test_overlapping_full_port_signals_are_not_primary_strategy_return() -> None:
    base = pd.Timestamp("2025-01-02 15:00:00", tz="UTC")
    trades = pd.DataFrame(
        {
            "candidate_id": ["bad_overlap"] * 10,
            "symbol": ["TQQQ"] * 10,
            "entry_ts": [base + pd.Timedelta(minutes=i) for i in range(10)],
            "exit_ts": [base + pd.Timedelta(hours=1)] * 10,
            "source_return": [0.10] * 10,
        }
    )

    metrics, _, _ = validate_trade_ledger(trades)
    raw = metrics[(metrics["candidate_id"] == "bad_overlap") & (metrics["portfolio_variant"] == RAW_VARIANT)].iloc[0]
    primary = metrics[(metrics["candidate_id"] == "bad_overlap") & (metrics["portfolio_variant"] == PRIMARY_VARIANT)].iloc[0]

    assert math.isclose(raw["compounded_total_x"], 1.1**10)
    assert raw["raw_full_size_impossible"]
    assert raw["max_concurrent_positions"] == 10
    assert raw["max_same_day_signals"] == 10

    assert primary["portfolio_events"] == 1
    assert math.isclose(primary["simple_total_return"], 0.10)
    assert math.isclose(primary["return_on_deployed_capital"], 0.10)
    assert math.isclose(primary["compounded_total_x"], 1.10)


def test_equal_split_same_day_caps_total_deployment() -> None:
    base = pd.Timestamp("2025-01-02 15:00:00", tz="UTC")
    trades = pd.DataFrame(
        {
            "candidate_id": ["scalp"] * 10,
            "symbol": ["TQQQ"] * 10,
            "entry_ts": [base + pd.Timedelta(minutes=i) for i in range(10)],
            "exit_ts": [base + pd.Timedelta(minutes=i + 1) for i in range(10)],
            "source_return": [0.10] * 10,
        }
    )

    metrics, _, events = validate_trade_ledger(trades)
    split = metrics[(metrics["candidate_id"] == "scalp") & (metrics["portfolio_variant"] == "equal_split_same_day_100pct")].iloc[0]
    split_events = events[(events["candidate_id"] == "scalp") & (events["portfolio_variant"] == "equal_split_same_day_100pct")]

    assert split["portfolio_events"] == 1
    assert math.isclose(split["simple_total_return"], 0.10)
    assert math.isclose(split["capital_deployed_turnover"], 1.0)
    assert split_events.iloc[0]["source_signal_count"] == 10
