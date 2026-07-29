import pandas as pd

from cam0006 import (
    allocate_daily,
    is_probable_split_ratio,
    marketable_long_return,
    max_drawdown_and_recovery,
    protected_long_exit,
    protected_short_return,
    select_official_open,
)


def test_selects_largest_matched_official_open():
    records = [
        {"c": "O", "p": 10.0, "s": 100, "t": "x1", "x": "P"},
        {"c": "Q", "p": 10.0, "s": 100, "t": "x2", "x": "P"},
        {"c": "O", "p": 10.1, "s": 5000, "t": "x3", "x": "Q"},
        {"c": "Q", "p": 10.1, "s": 5000, "t": "x4", "x": "Q"},
    ]
    selected, status = select_official_open(records)
    assert status == "selected"
    assert selected["exchange"] == "Q"
    assert selected["price"] == 10.1
    assert selected["size"] == 5000


def test_rejects_unmatched_or_ambiguous_official_open():
    unmatched = [{"c": "Q", "p": 10.0, "s": 100, "t": "x", "x": "Q"}]
    assert select_official_open(unmatched)[1] == "official_q_without_matching_open"
    ambiguous = [
        {"c": "O", "p": 10.0, "s": 100, "t": "a", "x": "Q"},
        {"c": "Q", "p": 10.0, "s": 100, "t": "b", "x": "Q"},
        {"c": "O", "p": 10.1, "s": 100, "t": "c", "x": "N"},
        {"c": "Q", "p": 10.1, "s": 100, "t": "d", "x": "N"},
    ]
    assert select_official_open(ambiguous)[1] == "ambiguous_maximum_official_open"


def test_cost_and_protected_short_path():
    assert abs(marketable_long_return(100, 101, 10) - 0.008) < 1e-12
    pnl, stopped, exit_price = protected_short_return(
        100, 98, [100.5, 102.1, 99], 0.02, 10, 10
    )
    assert stopped
    assert abs(exit_price - 102.102) < 1e-12
    assert pnl < -0.02


def test_protected_long_uses_adverse_gap_or_stop_fill():
    exit_price, stopped = protected_long_exit(
        100, 105, [(100, 99.5), (98.5, 98.0)], 0.01, 10
    )
    assert stopped
    assert abs(exit_price - (98.5 * 0.999)) < 1e-12
    exit_price, stopped = protected_long_exit(
        100, 105, [(100, 99.5), (99.5, 98.9)], 0.01, 10
    )
    assert stopped
    assert abs(exit_price - (99.0 * 0.999)) < 1e-12
    assert protected_long_exit(100, 105, [(100, 99.5)], None) == (105.0, False)


def test_balanced_allocation_keeps_half_per_side():
    assert abs(allocate_daily([0.02, 0.04], [-0.01], "long_only") - 0.03) < 1e-12
    assert abs(allocate_daily([0.02, 0.04], [-0.01], "balanced") - 0.01) < 1e-12
    assert abs(allocate_daily([], [0.02], "balanced") - 0.01) < 1e-12


def test_fixed_base_drawdown_and_recovery():
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"]
            ),
            "net_pnl": [0.10, -0.05, -0.05, 0.11],
        }
    )
    drawdown, days, unresolved = max_drawdown_and_recovery(daily)
    assert abs(drawdown - (0.10 / 1.10)) < 1e-12
    assert days == 1
    assert not unresolved


def test_probable_split_ratio_does_not_reject_large_real_gap():
    assert is_probable_split_ratio(100, 1000)
    assert is_probable_split_ratio(100, 2500)
    assert not is_probable_split_ratio(75, 100)
    assert not is_probable_split_ratio(130, 100)
