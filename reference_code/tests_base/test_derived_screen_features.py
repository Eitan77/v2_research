from __future__ import annotations

import pandas as pd

from ar_pipeline.data import _derived_feature_inputs, add_derived_features
from ar_pipeline.engines.cuda_discovery import _parse_nvidia_smi_sample


def test_bar_screen_derived_features_resolve_to_physical_inputs() -> None:
    features = ["bar_return", "session_open_return", "close_vs_vwap", "close_in_bar_range"]
    inputs = _derived_feature_inputs(features)
    assert inputs == {
        "bar_return": "open",
        "session_open_return": "open",
        "close_vs_vwap": "vwap",
        "close_in_bar_range": "high",
        "close_in_bar_range_low": "low",
    }
    frame = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA"],
            "timestamp": pd.to_datetime(["2026-01-02 14:30:00Z", "2026-01-02 14:45:00Z"]),
            "open": [100.0, 101.0],
            "high": [102.0, 104.0],
            "low": [99.0, 100.0],
            "close": [101.0, 103.0],
            "vwap": [100.5, 102.0],
        }
    )
    result = add_derived_features(frame, features)
    assert result["bar_return"].notna().all()
    assert result["session_open_return"].iloc[0] == result["bar_return"].iloc[0]
    assert result["close_vs_vwap"].notna().all()
    assert result["close_in_bar_range"].between(0, 1).all()


def test_nvidia_sampler_keeps_utilization_when_power_is_unavailable() -> None:
    util, power = _parse_nvidia_smi_sample("87, [N/A]")
    assert util == 87
    assert pd.isna(power)
