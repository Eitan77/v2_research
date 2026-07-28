"""Explicit, fingerprinted human/agent promotion gates.

The pipeline can rank or flag candidates automatically, but cannot turn an
eligibility score into an execution-data request on its own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from .config import read_structured
from .contracts import fingerprint
from .manifest import utc_now
from .validation import SafetyGateError


def approve_quote_fill(
    run_path: str | Path,
    candidate_ids: Iterable[str],
    *,
    rationale: str,
    reviewer: str = "operator",
) -> Path:
    path = Path(run_path)
    selected = sorted({str(candidate).strip() for candidate in candidate_ids if str(candidate).strip()})
    if not selected:
        raise SafetyGateError("at least one candidate is required for quote-fill approval")
    if not rationale.strip():
        raise SafetyGateError("quote-fill approval requires a written rationale")
    review_path = path / "stage_03_promotion_review" / "promotion_review_queue.csv"
    if not review_path.exists():
        raise FileNotFoundError("run Stage 3 before approving quote validation")
    review = pd.read_csv(review_path)
    id_col = "base_candidate_id" if "base_candidate_id" in review.columns else "candidate_id"
    known = set(review[id_col].astype(str))
    unknown = sorted(set(selected) - known)
    if unknown:
        raise SafetyGateError(f"candidate(s) are not in the Stage 3 review queue: {unknown}")
    if "screening_eligibility" in review.columns:
        eligible = set(review.loc[review["screening_eligibility"].astype(bool), id_col].astype(str))
        not_eligible = sorted(set(selected) - eligible)
        if not_eligible:
            raise SafetyGateError(f"candidate(s) did not pass the screening eligibility gate: {not_eligible}")
    now = utc_now()
    mask = review[id_col].astype(str).isin(selected)
    review.loc[mask, "agent_decision"] = "quote_fill"
    review.loc[mask, "agent_rationale"] = rationale.strip()
    review.loc[mask, "agent_reviewed_at"] = now
    review.loc[mask, "agent_reviewer"] = reviewer.strip() or "operator"
    review.to_csv(review_path, index=False)
    config = read_structured(path / "scan.yaml")
    approval = {
        "kind": "quote_fill_approval",
        "created_at": now,
        "reviewer": reviewer.strip() or "operator",
        "rationale": rationale.strip(),
        "candidate_ids": selected,
        "config_fingerprint": fingerprint(config),
        "review_queue": str(review_path),
    }
    approval_path = path / "stage_03_promotion_review" / "quote_fill_approval.json"
    approval_path.write_text(json.dumps(approval, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return approval_path


def approved_quote_candidates(run_path: str | Path) -> set[str]:
    path = Path(run_path)
    approval_path = path / "stage_03_promotion_review" / "quote_fill_approval.json"
    if not approval_path.exists():
        raise SafetyGateError("quote validation is blocked: no signed Stage 3 quote-fill approval exists")
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    config = read_structured(path / "scan.yaml")
    if approval.get("config_fingerprint") != fingerprint(config):
        raise SafetyGateError("quote validation is blocked: scan.yaml changed after approval")
    candidates = {str(value) for value in approval.get("candidate_ids", []) if str(value)}
    if not candidates:
        raise SafetyGateError("quote validation is blocked: approval contains no candidates")
    return candidates
