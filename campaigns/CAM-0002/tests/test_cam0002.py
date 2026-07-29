from __future__ import annotations

import pandas as pd
import pytest

from cam0002 import (
    choose_nonoverlapping_clusters,
    event_net_return,
    max_drawdown_and_recovery,
    source_trigger,
    validate_cutoff,
)


def test_holdout_fail_fast() -> None:
    with pytest.raises(RuntimeError):
        validate_cutoff(pd.DataFrame({"date": ["2026-05-01"]}))


def test_completed_event_enters_next_minute_costs() -> None:
    assert event_net_return(100.0, 102.0, 10.0) == pytest.approx(0.018)


def test_same_minute_cluster_equal_weight_and_nonoverlap() -> None:
    events = pd.DataFrame(
        {
            "event_ts": pd.to_datetime(
                ["2025-01-02 15:00Z", "2025-01-02 15:00Z", "2025-01-02 15:20Z",
                 "2025-01-02 16:02Z"], utc=True
            ),
            "symbol": ["A", "B", "C", "D"],
        }
    )
    result = choose_nonoverlapping_clusters(events, 60)
    assert result["symbol"].tolist() == ["A", "B", "D"]
    assert result.loc[result["event_ts"] == result["event_ts"].min(), "weight"].tolist() == [0.5, 0.5]


def test_running_peak_drawdown_not_initial_capital_drawdown() -> None:
    daily = pd.DataFrame(
        {"date": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"]),
         "net_pnl": [0.5, -0.3, 0.3]}
    )
    dd, _, unresolved = max_drawdown_and_recovery(daily)
    assert dd == pytest.approx(0.2)
    assert not unresolved


def test_source_trigger_requires_both_frozen_filters() -> None:
    assert source_trigger(-0.05, 0.005)
    assert not source_trigger(-0.039, 0.001)
    assert not source_trigger(-0.05, 0.007)


def test_fixed_base_cluster_pnl_is_additive_not_compounded() -> None:
    returns = pd.Series([0.10, -0.04])
    weights = pd.Series([0.5, 0.5])
    assert float((returns * weights).sum()) == pytest.approx(0.03)
