from __future__ import annotations

import pandas as pd

from alpaca_research.technical import resample_bars


def test_rth_resample_is_left_labelled_and_excludes_premarket() -> None:
    timestamps = pd.date_range("2025-01-02 14:26:00Z", periods=10, freq="1min")
    bars = pd.DataFrame(
        {
            "symbol": ["QQQ"] * len(timestamps),
            "timestamp": timestamps,
            "open": list(range(100, 110)),
            "high": list(range(101, 111)),
            "low": list(range(99, 109)),
            "close": list(range(100, 110)),
            "volume": [10] * len(timestamps),
            "trade_count": [1] * len(timestamps),
            "vwap": list(range(100, 110)),
            "feed": ["sip"] * len(timestamps),
            "adjustment": ["raw"] * len(timestamps),
        }
    )
    derived = resample_bars(bars, "5m")
    assert len(derived) == 1
    row = derived.iloc[0]
    assert row["timestamp"] == pd.Timestamp("2025-01-02 14:30:00Z")
    assert row["open"] == 104.0
    assert row["close"] == 108.0
    assert row["bar_end_ts"] == pd.Timestamp("2025-01-02 14:35:00Z")
