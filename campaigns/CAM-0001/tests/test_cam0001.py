from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from cam0001 import (
    CUTOFF,
    RunConfig,
    _max_drawdown_and_recovery,
    simulate,
    simulate_invalidation,
    simulate_price_stop,
)


def fixture_frame(periods: int = 230) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=periods)
    rows = []
    for symbol, step in [("QQQ", 0.001), ("TQQQ", 0.003), ("SOXL", 0.002)]:
        prior_close = 100.0
        for i, date in enumerate(dates):
            open_px = prior_close * (1.0 + step / 3.0)
            close_px = open_px * (1.0 + 2.0 * step / 3.0)
            rows.append(
                {
                    "symbol": symbol,
                    "date": date,
                    "open": open_px,
                    "high": max(open_px, close_px),
                    "low": min(open_px, close_px),
                    "close": close_px,
                }
            )
            prior_close = close_px
    return pd.DataFrame(rows)


def test_signal_uses_completed_close_and_next_open() -> None:
    frame = fixture_frame()
    cfg = RunConfig(lookback=20, market_sma=200, holding_sessions=5)
    _, trades, _ = simulate(frame, cfg)
    assert len(trades) > 0
    first = trades.iloc[0]
    dates = sorted(frame["date"].unique())
    decision_i = dates.index(first["decision_date"].to_datetime64())
    entry_i = dates.index(first["entry_date"].to_datetime64())
    exit_i = dates.index(first["exit_date"].to_datetime64())
    assert entry_i == decision_i + 1
    assert exit_i == entry_i + 5
    assert first["symbol"] == "TQQQ"


def test_fixed_base_additive_pnl_and_costs() -> None:
    frame = fixture_frame()
    cfg = RunConfig(lookback=20, market_sma=200, holding_sessions=5, cost_bps_per_side=5)
    daily, trades, metrics = simulate(frame, cfg)
    expected = float(trades["gross_return_contribution"].sum() - 0.001 * trades["entry_date"].nunique())
    assert daily["net_pnl"].sum() == pytest.approx(expected)
    assert metrics["net_full_period_simple_return"] == pytest.approx(expected)
    assert daily["equity"].iloc[-1] == pytest.approx(1.0 + expected)
    assert metrics["maximum_gross_exposure"] == 1.0


def test_running_peak_drawdown_not_fixed_base_giveback() -> None:
    daily = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=4),
            "net_pnl": [9.0, 90.0, -10.0, 20.0],
        }
    )
    max_dd, _, unresolved = _max_drawdown_and_recovery(daily)
    assert max_dd == pytest.approx(0.10)
    assert unresolved is False


def test_holdout_fails_fast() -> None:
    frame = fixture_frame()
    frame.loc[frame.index[-1], "date"] = CUTOFF + pd.Timedelta(days=1)
    with pytest.raises(RuntimeError, match="holdout"):
        simulate(frame, RunConfig())


def test_invalidation_uses_close_then_next_open() -> None:
    frame = fixture_frame(periods=30)
    dates = sorted(frame["date"].unique())
    entry_date = dates[2]
    tqqq = (frame["symbol"] == "TQQQ") & (frame["date"] == entry_date)
    prior_close = frame.loc[
        (frame["symbol"] == "TQQQ") & (frame["date"] == dates[1]), "close"
    ].iloc[0]
    frame.loc[tqqq, ["close", "low"]] = prior_close * 0.90
    config = RunConfig(
        trade_symbols=("TQQQ",), lookback=1, market_sma=2,
        holding_sessions=10, breadth=1,
    )
    _, trades, _ = simulate_invalidation(frame, config, "fund_momentum")
    first = trades.iloc[0]
    assert first["entry_date"] == entry_date
    assert first["exit_date"] == dates[3]
    assert first["exit_reason"] == "fund_momentum"


def test_price_stop_uses_gap_open_not_stop_trigger() -> None:
    frame = fixture_frame(periods=30)
    dates = sorted(frame["date"].unique())
    gap_date = dates[3]
    mask = (frame["symbol"] == "TQQQ") & (frame["date"] == gap_date)
    frame.loc[mask, ["open", "high", "low", "close"]] *= 0.50
    config = RunConfig(
        trade_symbols=("TQQQ",), lookback=1, market_sma=2,
        holding_sessions=10, breadth=1,
    )
    _, trades, _ = simulate_price_stop(frame, config, 0.10)
    first = trades.iloc[0]
    expected_open = frame.loc[mask, "open"].iloc[0]
    assert first["exit_date"] == gap_date
    assert first["exit_price"] == pytest.approx(expected_open)
    assert first["exit_reason"] == "gap_stop"
