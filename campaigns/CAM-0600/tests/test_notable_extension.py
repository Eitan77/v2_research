from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / "campaigns" / "CAM-0600" / "artifacts"


def report(run_id: str) -> dict:
    return json.loads((ARTIFACTS / run_id / "execution_report.json").read_text())


def test_confirmation_reconciles_and_respects_holdout():
    data = report("RUN-0032")
    assert data["status"] == "passed"
    assert data["maximum_loaded_date"] == "2026-04-30"
    assert data["holdout_rows_loaded"] == 0
    assert all(item["passed"] for item in data["candidates"])


def test_neighborhood_and_quote_replays_are_complete():
    neighborhood = report("RUN-0035")
    assert neighborhood["holdout_rows_loaded"] == 0
    assert len(neighborhood["neighborhoods"]) == 7
    assert neighborhood["sndk_data_integrity"]["price_observations_before_membership"] == 199
    for run_id in ("RUN-0036", "RUN-0039", "RUN-0040"):
        data = report(run_id)
        assert data["status"] == "completed"
        assert data["holdout_rows_loaded"] == 0
        assert min(row["role_coverage"] for row in data["metrics"]) == 1.0


def test_selected_smoothed_candidate_is_profitable_and_cadence_compliant():
    rows = report("RUN-0040")["metrics"]
    at2 = next(row for row in rows if row["extra_adverse_bps_per_side"] == 2.0)
    at5 = next(row for row in rows if row["extra_adverse_bps_per_side"] == 5.0)
    assert at2["net_simple_return"] > 1.0
    assert at2["maximum_drawdown"] < 0.11
    assert at2["positive_months"] == 9
    assert at2["trade_session_fraction"] >= 0.5
    assert at5["net_simple_return"] > 0.99


def test_concentration_audit_rejects_duplicate_and_top_five_dependent_results():
    audit = report("RUN-0037")
    assert audit["holdout_rows_loaded"] == 0
    assert audit["key_correlations"]["ma_top10_p3_vs_dual_top10_p3"] > 0.99
    triple = next(row for row in audit["concentration"] if row["candidate"] == "triple_ma10_50_200_top3")
    strict = next(row for row in audit["concentration"] if row["candidate"] == "ma200_top5_history252_p5")
    assert triple["leave_top5_out_return"] < 0
    assert strict["leave_top5_out_return"] > 0
