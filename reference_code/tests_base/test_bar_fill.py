from __future__ import annotations

import math

import pandas as pd
import pytest

from ar_pipeline.contracts import BarTiming
from ar_pipeline.engines.bar_fill import BarFillPolicy, simulate_bar_fills


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["AAA"] * 5,
            "timestamp": pd.date_range("2026-01-02 14:30:00Z", periods=5, freq="15min"),
            "open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [101.0, 103.0, 104.0, 105.0, 106.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0],
            "close": [100.5, 102.0, 103.0, 104.0, 105.0],
            "volume": [10_000] * 5,
        }
    )


def test_completed_start_labelled_bar_enters_only_on_next_actionable_open() -> None:
    signals = pd.DataFrame(
        {"candidate_id": ["c"], "symbol": ["AAA"], "signal_ts": ["2026-01-02 14:30:00Z"], "side": ["long"]}
    )
    policy = BarFillPolicy(BarTiming("15m", "start", decision_latency_ms=0), holding_bars=1)
    result = simulate_bar_fills(_bars(), signals, policy).iloc[0]
    assert result["bar_fill_status"] == "filled"
    assert result["signal_available_ts"] == pd.Timestamp("2026-01-02 14:45:00Z")
    assert result["entry_ts"] == pd.Timestamp("2026-01-02 14:45:00Z")
    assert result["exit_ts"] == pd.Timestamp("2026-01-02 15:00:00Z")
    assert math.isclose(result["bar_return"], 102.0 / 101.0 - 1.0)


def test_nonzero_latency_does_not_claim_an_opening_price_already_in_the_past() -> None:
    signals = pd.DataFrame(
        {"candidate_id": ["c"], "symbol": ["AAA"], "signal_ts": ["2026-01-02 14:30:00Z"], "side": ["long"]}
    )
    policy = BarFillPolicy(BarTiming("15m", "start", decision_latency_ms=1), holding_bars=1)
    result = simulate_bar_fills(_bars(), signals, policy).iloc[0]
    assert result["entry_ts"] == pd.Timestamp("2026-01-02 15:00:00Z")
    assert result["bar_entry_price"] == 102.0


def test_stop_and_target_same_bar_uses_worst_case_or_rejects() -> None:
    signals = pd.DataFrame(
        {
            "candidate_id": ["c"],
            "symbol": ["AAA"],
            "signal_ts": ["2026-01-02 14:30:00Z"],
            "side": ["long"],
            "stop_price": [100.5],
            "take_profit_price": [102.5],
        }
    )
    worst_case = simulate_bar_fills(
        _bars(), signals, BarFillPolicy(BarTiming("15m", "start"), holding_bars=1, intrabar_ambiguity="worst_case")
    ).iloc[0]
    assert worst_case["bar_fill_status"] == "filled"
    assert worst_case["bar_exit_reason"] == "ambiguous_stop_target_worst_case"
    assert worst_case["exit_ref_price"] == 100.5

    rejected = simulate_bar_fills(
        _bars(), signals, BarFillPolicy(BarTiming("15m", "start"), holding_bars=1, intrabar_ambiguity="reject")
    ).iloc[0]
    assert rejected["bar_fill_status"] == "unfilled"
    assert rejected["bar_exit_reason"] == "ambiguous_stop_target"


def test_bar_fill_refuses_cross_session_holds() -> None:
    bars = _bars().iloc[:2].copy()
    bars.loc[1, "timestamp"] = pd.Timestamp("2026-01-05 14:30:00Z")
    signals = pd.DataFrame(
        {"candidate_id": ["c"], "symbol": ["AAA"], "signal_ts": ["2026-01-02 14:30:00Z"], "side": ["long"]}
    )
    result = simulate_bar_fills(bars, signals, BarFillPolicy(BarTiming("15m", "start"), holding_bars=1)).iloc[0]
    assert result["bar_fill_status"] == "unfilled"
    assert result["bar_exit_reason"] in {"would_cross_session", "insufficient_future_bars", "no_immediate_actionable_entry_bar"}
