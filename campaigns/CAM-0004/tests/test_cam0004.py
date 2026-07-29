from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cam0004 import (
    assign_tail_portfolios,
    build_daily_features,
    long_net_return,
    max_drawdown_and_recovery,
    paper_rank_normalize,
    protected_short_net_return,
    source_style_residual,
    validate_cutoff,
)
from run0004 import build_signals, expanding_prior_flag
from run0005 import apply_round_trip_cost, weighted_mean


def test_holdout_fail_fast() -> None:
    with pytest.raises(RuntimeError):
        validate_cutoff(pd.DataFrame({"date": ["2026-05-01"]}))


def test_paper_rank_normalization() -> None:
    values = pd.Series([30.0, 10.0, 20.0], index=["c", "a", "b"])
    result = paper_rank_normalize(values)
    assert result.sum() == pytest.approx(0.0)
    assert result.abs().sum() == pytest.approx(1.0)
    assert result["a"] < result["b"] < result["c"]


def test_intercept_remains_in_residual() -> None:
    x = pd.DataFrame({"x": [-1.0, 0.0, 1.0, 2.0, 3.0]})
    y = 0.02 + 0.5 * x["x"]
    risk, residual, alpha = source_style_residual(y, x)
    assert alpha == pytest.approx(0.02)
    assert np.nanmax(np.abs(risk - 0.5 * x["x"])) < 1e-12
    assert np.nanmax(np.abs(residual - 0.02)) < 1e-12


def test_tail_assignment_is_complete() -> None:
    residual = pd.Series(np.arange(20.0))
    groups = assign_tail_portfolios(residual, groups=10)
    assert groups.value_counts().sort_index().tolist() == [2] * 10


def test_running_peak_drawdown() -> None:
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"]),
            "net_pnl": [0.5, -0.3, 0.3],
        }
    )
    drawdown, _, unresolved = max_drawdown_and_recovery(daily)
    assert drawdown == pytest.approx(0.2)
    assert not unresolved


def test_daily_beta_shape_and_availability_shift() -> None:
    dates = pd.date_range("2024-01-01", periods=65, freq="D")
    qqq_close = 100.0 * (1.001 ** np.arange(len(dates)))
    stock_close = 50.0 * (1.002 ** np.arange(len(dates)))
    daily = pd.concat(
        [
            pd.DataFrame(
                {
                    "symbol": "QQQ",
                    "date": dates,
                    "close": qqq_close,
                    "volume": 1_000_000,
                }
            ),
            pd.DataFrame(
                {
                    "symbol": "A",
                    "date": dates,
                    "close": stock_close,
                    "volume": 500_000,
                }
            ),
        ],
        ignore_index=True,
    )
    result = build_daily_features(daily)
    assert len(result) == len(daily)
    assert result[result["symbol"] == "A"]["beta_60d"].notna().sum() > 0
    assert pd.isna(result[result["symbol"] == "A"].iloc[0]["reversal_1d"])


def test_following_bar_cost_and_short_stop() -> None:
    assert long_net_return(100.0, 101.0, 5.0) == pytest.approx(0.009)
    result, stopped, exit_ = protected_short_net_return(
        entry=100.0,
        scheduled_exit=99.0,
        path_high=102.5,
        cost_bps_per_side=5.0,
        stop_fraction=0.02,
        stop_slippage_bps=5.0,
    )
    assert stopped
    assert exit_ == pytest.approx(102.0 * 1.0005)
    assert result < -0.021


def test_expanding_state_threshold_is_shifted() -> None:
    values = pd.Series(np.arange(45.0))
    flags = expanding_prior_flag(values)
    assert not flags.iloc[:40].any()
    assert flags.iloc[-1]


def test_cumulative_formation_compounds_within_session() -> None:
    symbols = [f"S{i:02d}" for i in range(30)]
    rows = []
    for symbol_index, symbol in enumerate(symbols):
        for period, residual in enumerate(
            [0.01 + symbol_index / 10_000, -0.005]
        ):
            rows.append(
                {
                    "date": "2025-01-02",
                    "symbol": symbol,
                    "decision_ts": pd.Timestamp(
                        "2025-01-02 15:00:00Z"
                    )
                    + pd.Timedelta(minutes=30 * period),
                    "residual": residual,
                    "volatility_20d": 0.02,
                }
            )
    result = build_signals(pd.DataFrame(rows))
    second = result[result["symbol"].eq("S00")].iloc[1]
    assert second["score_K2"] == pytest.approx(1.01 * 0.995 - 1.0)
    assert pd.isna(result[result["symbol"].eq("S00")].iloc[0]["score_K2"])


def test_cost_and_capped_signal_weighting() -> None:
    assert apply_round_trip_cost(0.001, 3) == pytest.approx(0.0004)
    values = pd.Series([0.01, 0.02, 0.03])
    scores = pd.Series([1.0, 1.0, 100.0])
    capped = weighted_mean(values, scores, "strength_capped")
    assert values.mean() < capped < values.iloc[-1]
