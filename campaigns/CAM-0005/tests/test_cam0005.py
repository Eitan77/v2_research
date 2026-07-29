import pandas as pd
import pytest

from cam0005 import (
    allocate_pair_pnl,
    direction_product,
    marketable_long_return,
    max_drawdown_and_recovery,
    rolling_prior_quantile,
    validate_cutoff,
)


def test_holdout_fails_fast() -> None:
    with pytest.raises(RuntimeError):
        validate_cutoff(pd.DataFrame({"session": ["2026-05-01"]}))


def test_continuation_and_reversal_product_mapping() -> None:
    assert direction_product(0.01, "continuation", "TQQQ", "SQQQ") == "TQQQ"
    assert direction_product(-0.01, "continuation", "TQQQ", "SQQQ") == "SQQQ"
    assert direction_product(0.01, "reversal", "TQQQ", "SQQQ") == "SQQQ"


def test_round_trip_cost_and_pair_allocation() -> None:
    assert marketable_long_return(100.0, 101.0, 5.0) == pytest.approx(0.009)
    assert allocate_pair_pnl([0.02, -0.01]) == pytest.approx(0.005)
    assert allocate_pair_pnl([]) == 0.0


def test_fixed_base_drawdown() -> None:
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"]),
            "net_pnl": [0.5, -0.3, 0.3],
        }
    )
    drawdown, _, unresolved = max_drawdown_and_recovery(daily)
    assert drawdown == pytest.approx(0.2)
    assert not unresolved


def test_next_session_is_not_calendar_next_day() -> None:
    sessions = pd.Series(pd.to_datetime(["2025-01-03", "2025-01-06"]))
    next_session = sessions.shift(-1)
    assert (next_session.iloc[0] - sessions.iloc[0]).days == 3


def test_magnitude_threshold_uses_only_prior_sessions() -> None:
    values = pd.Series(range(50), dtype=float)
    threshold = rolling_prior_quantile(values, 0.8)
    assert threshold.iloc[:40].isna().all()
    assert threshold.iloc[40] < values.iloc[40]
