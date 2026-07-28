"""Chronological robustness and data-snooping diagnostics.

These reports are gates and diagnostics, not a licence to keep tuning.  Every
row in the discovery leaderboard is counted as a trial, and walk-forward
selection only uses information that was available before each test fold.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
import json
from math import comb
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from ar_pipeline.validation import SafetyGateError


@dataclass(frozen=True)
class WalkForwardFold:
    fold: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    embargo_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def make_expanding_folds(
    timestamps: pd.Series,
    *,
    initial_train_days: int,
    test_days: int,
    embargo_days: int,
) -> list[WalkForwardFold]:
    days = pd.Series(pd.to_datetime(timestamps, utc=True, errors="coerce").dropna()).dt.normalize().drop_duplicates().sort_values().tolist()
    if initial_train_days < 1 or test_days < 1 or embargo_days < 0:
        raise SafetyGateError("walk-forward day counts must be positive (embargo may be zero)")
    folds: list[WalkForwardFold] = []
    test_start_index = initial_train_days + embargo_days
    fold = 1
    while test_start_index < len(days):
        test_end_index = min(test_start_index + test_days - 1, len(days) - 1)
        train_end_index = test_start_index - embargo_days - 1
        if train_end_index < 0:
            break
        folds.append(
            WalkForwardFold(
                fold=fold,
                train_start=days[0],
                train_end=days[train_end_index] + pd.Timedelta(days=1),
                embargo_end=days[test_start_index],
                test_start=days[test_start_index],
                test_end=days[test_end_index] + pd.Timedelta(days=1),
            )
        )
        fold += 1
        test_start_index = test_end_index + 1
    return folds


def walk_forward_selection(
    trades: pd.DataFrame,
    *,
    return_col: str,
    candidate_col: str = "candidate_id",
    initial_train_days: int = 252,
    test_days: int = 63,
    embargo_days: int = 5,
    min_train_trades: int = 30,
) -> tuple[pd.DataFrame, pd.DataFrame, list[WalkForwardFold]]:
    required = {candidate_col, "entry_ts", "exit_ts", return_col}
    missing = sorted(required - set(trades.columns))
    if missing:
        raise SafetyGateError(f"walk-forward ledger missing columns: {missing}")
    work = trades.copy()
    work["entry_ts"] = pd.to_datetime(work["entry_ts"], utc=True, errors="coerce", format="mixed")
    work["exit_ts"] = pd.to_datetime(work["exit_ts"], utc=True, errors="coerce", format="mixed")
    work[return_col] = pd.to_numeric(work[return_col], errors="coerce")
    work = work.dropna(subset=[candidate_col, "entry_ts", "exit_ts", return_col]).copy()
    work = work[(work["exit_ts"] > work["entry_ts"]) & np.isfinite(work[return_col].astype(float))].copy()
    if work.empty:
        return pd.DataFrame(), pd.DataFrame(), []
    folds = make_expanding_folds(
        work["entry_ts"], initial_train_days=initial_train_days, test_days=test_days, embargo_days=embargo_days
    )
    selection_rows: list[dict[str, Any]] = []
    oos_parts: list[pd.DataFrame] = []
    for fold in folds:
        # The strict exit rule embargoes labels/outcomes that could overlap the
        # test window, not merely signals whose entry happened earlier.
        train = work[work["exit_ts"] < fold.test_start].copy()
        test = work[(work["entry_ts"] >= fold.test_start) & (work["exit_ts"] < fold.test_end)].copy()
        summary = (
            train.groupby(candidate_col, sort=True)[return_col]
            .agg(train_trades="count", train_mean="mean", train_total="sum", train_std="std")
            .reset_index()
        )
        summary = summary[summary["train_trades"] >= min_train_trades].copy()
        if summary.empty:
            selection_rows.append({**asdict(fold), "status": "no_candidate_with_min_train_trades"})
            continue
        # Mean return ranks candidates without relying on the test period.
        selected = summary.sort_values(["train_mean", "train_total", candidate_col], ascending=[False, False, True]).iloc[0]
        candidate = str(selected[candidate_col])
        test_selected = test[test[candidate_col].astype(str).eq(candidate)].copy()
        test_returns = test_selected[return_col].astype(float)
        selection_rows.append(
            {
                **asdict(fold),
                "status": "selected",
                "selected_candidate_id": candidate,
                "train_trades": int(selected["train_trades"]),
                "train_mean_return": float(selected["train_mean"]),
                "test_trades": int(len(test_selected)),
                "test_mean_return": float(test_returns.mean()) if len(test_returns) else np.nan,
                "test_total_return": float((1.0 + test_returns).prod() - 1.0) if len(test_returns) else np.nan,
            }
        )
        if not test_selected.empty:
            test_selected = test_selected.copy()
            test_selected["walk_forward_fold"] = fold.fold
            test_selected["walk_forward_selected_candidate_id"] = candidate
            oos_parts.append(test_selected)
    selections = pd.DataFrame(selection_rows)
    oos = pd.concat(oos_parts, ignore_index=True) if oos_parts else pd.DataFrame()
    return selections, oos, folds


def combinatorial_pbo(daily_returns: pd.DataFrame, *, max_slices: int = 12) -> dict[str, Any]:
    """Estimate probability of backtest overfitting with CSCV-style splits.

    ``daily_returns`` has one column per candidate and chronologically ordered
    rows.  The result is deliberately called an estimate: it is meaningful only
    when all tested candidates, including discarded ones, are represented.
    """

    if daily_returns.empty or daily_returns.shape[1] < 2:
        return {"status": "insufficient_candidates", "pbo": np.nan, "splits": 0}
    returns = daily_returns.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    slices = min(max_slices, len(returns))
    if slices < 4:
        return {"status": "insufficient_time_slices", "pbo": np.nan, "splits": 0}
    if slices % 2:
        slices -= 1
    blocks = [block for block in np.array_split(np.arange(len(returns)), slices) if len(block)]
    choose = len(blocks) // 2
    combos = list(combinations(range(len(blocks)), choose))
    # The complementary split contains the same information mirrored; retain
    # one representative to avoid double counting while preserving chronology.
    seen: set[tuple[int, ...]] = set()
    logits: list[float] = []
    for train_ids in combos:
        test_ids = tuple(index for index in range(len(blocks)) if index not in train_ids)
        canonical = min(tuple(train_ids), test_ids)
        if canonical in seen:
            continue
        seen.add(canonical)
        train_rows = np.concatenate([blocks[index] for index in train_ids])
        test_rows = np.concatenate([blocks[index] for index in test_ids])
        train_score = returns.iloc[train_rows].sum(axis=0)
        selected = str(train_score.sort_values(ascending=False, kind="stable").index[0])
        test_score = returns.iloc[test_rows].sum(axis=0).sort_values(ascending=True, kind="stable")
        rank = int(np.flatnonzero(test_score.index.to_numpy() == selected)[0]) + 1
        percentile = rank / (len(test_score) + 1.0)
        logits.append(float(np.log(percentile / (1.0 - percentile))))
    if not logits:
        return {"status": "no_valid_splits", "pbo": np.nan, "splits": 0}
    return {
        "status": "ok",
        "pbo": float(np.mean(np.asarray(logits) <= 0.0)),
        "splits": len(logits),
        "logit_mean": float(np.mean(logits)),
        "slices": len(blocks),
        "candidate_count": int(returns.shape[1]),
    }


def multiple_testing_summary(trades: pd.DataFrame, *, return_col: str, candidate_col: str = "candidate_id") -> pd.DataFrame:
    work = trades.copy()
    work[return_col] = pd.to_numeric(work[return_col], errors="coerce")
    work = work[np.isfinite(work[return_col].astype(float))].copy()
    candidate_count = max(int(work[candidate_col].nunique()), 1)
    rows: list[dict[str, Any]] = []
    normal = NormalDist()
    for candidate, group in work.groupby(candidate_col, sort=True):
        values = group[return_col].astype(float).to_numpy()
        n = len(values)
        mean = float(np.mean(values)) if n else np.nan
        std = float(np.std(values, ddof=1)) if n > 1 else np.nan
        t_stat = mean / (std / np.sqrt(n)) if n > 1 and std > 0 else np.nan
        p_value = float(2.0 * (1.0 - normal.cdf(abs(t_stat)))) if np.isfinite(t_stat) else np.nan
        rows.append(
            {
                "candidate_id": str(candidate),
                "trades": n,
                "mean_return": mean,
                "sample_std": std,
                "normal_approx_t_stat": t_stat,
                "normal_approx_two_sided_p": p_value,
                "bonferroni_p": min(1.0, p_value * candidate_count) if np.isfinite(p_value) else np.nan,
                "trial_count": candidate_count,
            }
        )
    return pd.DataFrame(rows)


def run_robustness_review(
    trades: pd.DataFrame,
    leaderboard: pd.DataFrame,
    output_dir: Path,
    *,
    return_col: str,
    config: dict[str, Any],
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    wf_cfg = config.get("walk_forward", {}) if isinstance(config.get("walk_forward"), dict) else {}
    initial_days = int(wf_cfg.get("initial_train_days", 252))
    test_days = int(wf_cfg.get("test_days", 63))
    embargo_days = int(wf_cfg.get("embargo_days", 5))
    min_train_trades = int(wf_cfg.get("min_train_trades", 30))
    trial_columns = [column for column in ["candidate_id", "base_candidate_id", "family", "formula_id", "top_n", "holding_bars", "horizon", "cost_bps_per_side"] if column in leaderboard.columns]
    trials = leaderboard[trial_columns].drop_duplicates().copy() if trial_columns else pd.DataFrame()
    trials["trial_status"] = "evaluated" if not trials.empty else pd.Series(dtype=str)
    trials.to_csv(output_dir / "trial_ledger.csv", index=False)
    selections, oos, folds = walk_forward_selection(
        trades,
        return_col=return_col,
        initial_train_days=initial_days,
        test_days=test_days,
        embargo_days=embargo_days,
        min_train_trades=min_train_trades,
    )
    selections.to_csv(output_dir / "walk_forward_selection.csv", index=False)
    if not oos.empty:
        oos.to_parquet(output_dir / "walk_forward_oos_trades.parquet", index=False)
    daily = (
        trades.assign(_day=pd.to_datetime(trades["entry_ts"], utc=True).dt.normalize())
        .pivot_table(index="_day", columns="candidate_id", values=return_col, aggfunc="sum")
        .sort_index()
    )
    pbo = combinatorial_pbo(daily)
    (output_dir / "pbo_cscv.json").write_text(json.dumps(pbo, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    multiple = multiple_testing_summary(trades, return_col=return_col)
    multiple.to_csv(output_dir / "multiple_testing_summary.csv", index=False)
    lines = [
        "# Stage 6 Robustness Review",
        "",
        f"All counted discovery trials: {len(trials):,}",
        f"Walk-forward folds constructed: {len(folds):,}",
        f"Walk-forward selected folds: {int((selections.get('status', pd.Series(dtype=str)) == 'selected').sum()) if not selections.empty else 0:,}",
        f"CSCV-style PBO estimate: {pbo.get('pbo')}",
        "",
        "This is a diagnostic selection replay. A model adapter must still refit only on each training fold before a live-facing claim is allowed.",
        "",
    ]
    (output_dir / "robustness_report.md").write_text("\n".join(lines), encoding="utf-8")
    return {
        "trial_ledger": str(output_dir / "trial_ledger.csv"),
        "walk_forward": str(output_dir / "walk_forward_selection.csv"),
        "walk_forward_oos": str(output_dir / "walk_forward_oos_trades.parquet"),
        "pbo": str(output_dir / "pbo_cscv.json"),
        "multiple_testing": str(output_dir / "multiple_testing_summary.csv"),
        "report": str(output_dir / "robustness_report.md"),
    }
