from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import numpy as np
import pandas as pd


SCRIPT = Path(
    r"D:\AlgoResearch\research_pipeline\runs"
    r"\20260726_clean_weekly_xs_printer_apr2026_sealed"
    r"\clean_weekly_xs_search.py"
)
SPEC = spec_from_file_location("clean_weekly_xs_search", SCRIPT)
MODULE = module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_simple_return_is_arithmetic_not_compounded():
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    values = np.array([[0.10, -0.10, 0.05]], dtype=np.float32)
    result = MODULE.metric_frame(values, dates, np.ones(3, dtype=bool))
    assert np.isclose(result.loc[0, "simple_return"], 0.05)


def test_risk_target_is_causal():
    base = np.full((1, 40), 0.01, dtype=np.float32)
    changed = base.copy()
    changed[0, 30] = 0.50
    first = MODULE.apply_risk_target(base, 0.20)
    second = MODULE.apply_risk_target(changed, 0.20)
    # Changing day 30 cannot alter exposure or return on earlier days.
    np.testing.assert_allclose(first[:, :30], second[:, :30])


def test_weekly_rebalance_is_first_session_of_week():
    dates = pd.DatetimeIndex(
        ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"]
    )
    np.testing.assert_array_equal(
        MODULE.rebalance_mask(dates, "weekly"),
        np.array([True, False, True, False]),
    )


def test_registered_holdout_starts_april_2026():
    assert MODULE.RESEARCH_END == pd.Timestamp("2026-03-31")
    assert MODULE.HOLDOUT_START == pd.Timestamp("2026-04-01")
