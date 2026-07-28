from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ar_pipeline.approvals import approve_quote_fill, approved_quote_candidates
from ar_pipeline.config import write_structured
from ar_pipeline.contracts import BarTiming
from ar_pipeline.data import cross_sectional_feature_ranks
from ar_pipeline.validation import SafetyGateError, validate_run_config, validate_signal_ledger


def _safe_config(tmp_path: Path) -> dict:
    return {
        "schema_version": 2,
        "research": {"sealed_holdout": {"start": "2026-01-01", "end": "2026-12-31", "locked": True}},
        "data": {
            "catalog_path": str(tmp_path / "catalog.duckdb"),
            "table": "research_matrix",
            "feed": "sip",
            "adjustment": "raw",
            "bar_timestamp_label": "start",
            "universe": {"mode": "all"},
        },
        "scan": {
            "timeframe": "15m",
            "train_start": "2020-01-01",
            "train_end": "2025-12-31",
            "holding_bars": 1,
            "entry_model": "next_actionable_bar_open",
            "decision_latency_ms": 0,
        },
        "quote_fill": {"mode": "source_proxy_test_only"},
    }


def test_cross_sectional_ranks_do_not_change_when_future_rows_are_appended() -> None:
    historic = pd.DataFrame(
        {
            "signal_ts": [pd.Timestamp("2025-01-02 15:00:00Z")] * 3,
            "feature": [1.0, 2.0, 3.0],
        }
    )
    future = pd.concat(
        [historic, pd.DataFrame({"signal_ts": [pd.Timestamp("2026-01-02 15:00:00Z")] * 2, "feature": [-100.0, 10_000.0]})],
        ignore_index=True,
    )
    assert cross_sectional_feature_ranks(historic, ["feature"]).tolist() == cross_sectional_feature_ranks(future, ["feature"])[:3].tolist()


def test_config_requires_a_real_sealed_holdout_and_explicit_semantics(tmp_path: Path) -> None:
    config = _safe_config(tmp_path)
    assert validate_run_config(config).ok
    config["research"]["sealed_holdout"]["locked"] = False
    result = validate_run_config(config)
    assert not result.ok
    assert any("locked" in error for error in result.errors)


def test_signal_availability_cannot_precede_completion_of_a_start_labelled_bar() -> None:
    signals = pd.DataFrame(
        {
            "candidate_id": ["c"],
            "symbol": ["AAA"],
            "signal_ts": ["2026-01-02 14:30:00Z"],
            "signal_available_ts": ["2026-01-02 14:31:00Z"],
            "side": ["long"],
        }
    )
    with pytest.raises(SafetyGateError, match="before the source bar is complete"):
        validate_signal_ledger(signals, BarTiming("15m", "start"))


def test_quote_approval_is_required_and_invalidated_by_config_change(tmp_path: Path) -> None:
    config = _safe_config(tmp_path)
    write_structured(tmp_path / "scan.yaml", config)
    stage = tmp_path / "stage_03_promotion_review"
    stage.mkdir()
    pd.DataFrame({"base_candidate_id": ["candidate"], "screening_eligibility": [True]}).to_csv(stage / "promotion_review_queue.csv", index=False)
    with pytest.raises(SafetyGateError, match="no signed"):
        approved_quote_candidates(tmp_path)
    approve_quote_fill(tmp_path, ["candidate"], rationale="Cost and ledger reviewed.", reviewer="test")
    assert approved_quote_candidates(tmp_path) == {"candidate"}
    config["scan"]["holding_bars"] = 2
    write_structured(tmp_path / "scan.yaml", config)
    with pytest.raises(SafetyGateError, match="changed after approval"):
        approved_quote_candidates(tmp_path)
