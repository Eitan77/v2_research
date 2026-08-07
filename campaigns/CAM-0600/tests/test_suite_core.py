from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from suite_core import drawdown_metrics, rank_weights, semantic_fixtures


def test_semantic_fixtures() -> None:
    assert semantic_fixtures()["status"] == "passed"


def test_peak_relative_drawdown() -> None:
    equity = pd.Series([1.0, 2.0, 1.5, 2.1], index=pd.date_range("2020-01-01", periods=4))
    assert abs(drawdown_metrics(equity)["maximum_drawdown"] - 0.25) < 1e-12


def test_long_short_gross_and_neutrality() -> None:
    scores = np.asarray([[1.0, 2.0, 3.0, 4.0]])
    weights = rank_weights(scores, np.ones_like(scores, bool), [0], mode="long_short", quantile=0.25)
    assert abs(np.abs(weights).sum() - 1.0) < 1e-12
    assert abs(weights.sum()) < 1e-12
    assert weights[0, 0] < 0
    assert weights[0, -1] > 0
