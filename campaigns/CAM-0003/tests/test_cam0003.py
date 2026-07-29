import pandas as pd
import pytest

from cam0003 import max_drawdown_and_recovery, net_return, source_signal, validate_cutoff


def test_source_signal_uses_completed_morning_and_previous_close():
    assert source_signal(100.0, 101.0)
    assert not source_signal(100.0, 99.0)


def test_round_trip_cost_is_applied_to_entry_and_exit():
    assert net_return(100.0, 101.0, 10.0) == pytest.approx(
        101 * 0.999 / (100 * 1.001) - 1
    )


def test_holdout_fails_fast():
    with pytest.raises(RuntimeError):
        validate_cutoff(pd.DataFrame({"date": ["2026-05-01"]}))


def test_fixed_base_running_peak_drawdown():
    d = pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"]),
        "net_pnl": [0.10, -0.05, 0.06],
    })
    dd, _, unresolved = max_drawdown_and_recovery(d)
    assert dd == pytest.approx(0.05 / 1.10)
    assert not unresolved
