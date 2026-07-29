import pandas as pd

from cam0009 import (
    allocate_intraday,
    completed_window,
    max_drawdown_and_recovery,
    protected_long_return,
    protected_short_return,
    select_lagging_peers,
    shifted_rolling_median,
)


def test_completed_window_and_shift_are_causal():
    bars = pd.DataFrame(
        {"minute_number": [570, 571, 572, 573, 574], "close": range(5)}
    )
    assert len(completed_window(bars, 570, 5)) == 5
    assert completed_window(bars.drop(index=2), 570, 5) is None
    values = pd.Series([1.0, 2.0, 100.0])
    shifted = shifted_rolling_median(values, 2, 2)
    assert pd.isna(shifted.iloc[1])
    assert shifted.iloc[2] == 1.5


def test_peer_selection_excludes_leader_and_uses_completed_residual():
    frame = pd.DataFrame(
        {
            "symbol": ["LEAD", "LAG", "MOVED"],
            "residual_return": [0.02, 0.002, 0.015],
            "prior20_median_dollar_volume": [1e9, 8e8, 9e8],
        }
    )
    selected = select_lagging_peers(frame, "LEAD", 0.02, 0.5, 2)
    assert selected["symbol"].tolist() == ["LAG"]


def test_short_stop_and_forced_exit_costs():
    net, stopped, effective = protected_short_return(
        100.0, 98.0, [101.0, 102.1], 0.02, 5, 10
    )
    assert stopped
    assert round(effective, 6) == 102.102
    assert round(net, 6) == -0.02202
    net2, stopped2, effective2 = protected_short_return(
        100.0, 98.0, [101.0], 0.02, 5, 10
    )
    assert not stopped2
    assert effective2 == 98.0
    assert round(net2, 6) == 0.019


def test_long_stop_and_forced_exit_costs():
    net, stopped, effective = protected_long_return(
        100.0, 105.0, [99.0, 97.9, 101.0], 0.02, 5, 10
    )
    assert stopped
    assert round(effective, 6) == 97.902
    assert round(net, 6) == -0.02198
    net2, stopped2, effective2 = protected_long_return(
        100.0, 105.0, [99.0], None, 5, 10
    )
    assert not stopped2
    assert effective2 == 105.0
    assert round(net2, 6) == 0.049


def test_allocation_and_drawdown_include_original_capital_peak():
    candidates = pd.DataFrame(
        {
            "entry_timestamp": pd.to_datetime(
                ["2025-01-02 10:00", "2025-01-02 10:00"]
            ),
            "exit_timestamp": pd.to_datetime(
                ["2025-01-02 10:30", "2025-01-02 10:30"]
            ),
            "symbol": ["A", "B"],
            "leader_symbol": ["L", "L"],
        }
    )
    allocated = allocate_intraday(candidates, 0.6, 0.6, 1.0)
    assert allocated["position_fraction"].tolist() == [0.5, 0.5]
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
            "net_pnl": [-0.1, 0.1],
        }
    )
    drawdown, recovery, unresolved = max_drawdown_and_recovery(daily)
    assert round(drawdown, 6) == 0.1
    assert recovery == 1
    assert not unresolved
