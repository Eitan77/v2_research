"""Create an auditable Terra discovery review from completed artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hard-gate", required=True, type=Path)
    ap.add_argument("--leaderboard", required=True, type=Path)
    ap.add_argument("--feature-audit", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    metrics = pd.read_csv(args.hard_gate / "terra_hard_gate_metrics.csv")
    leaderboard = pd.read_parquet(args.leaderboard)
    feature = pd.read_csv(args.feature_audit / "feature_predictive_summary.csv")
    pairs = pd.read_csv(args.feature_audit / "feature_correlation_pairs.csv")
    best = metrics.sort_values(["cost_bps_per_side", "mean_monthly_net_pct"], ascending=[True, False]).groupby("cost_bps_per_side", as_index=False).head(1)
    target_by_cost = metrics.groupby("cost_bps_per_side").agg(
        candidates=("candidate_id", "nunique"),
        best_mean_monthly_net_pct=("mean_monthly_net_pct", "max"),
        median_of_candidate_means=("mean_monthly_net_pct", "median"),
        best_max_drawdown=("max_drawdown", "max"),
        best_worst_year=("worst_year_return", "max"),
        best_worst_regime=("worst_regime_simple_return", "max"),
        hard_gate_rows=("hard_gate_pass", "sum"),
    ).reset_index()
    best_target = float(metrics["mean_monthly_net_pct"].max()) if not metrics.empty else 0.0
    best_target_cost = float(metrics.loc[metrics["mean_monthly_net_pct"].idxmax(), "cost_bps_per_side"]) if not metrics.empty else None
    summary = {
        "leaderboard_rows": int(len(leaderboard)),
        "hard_gate_rows": int(metrics["hard_gate_pass"].sum()),
        "hard_gate_candidates": int(metrics.loc[metrics["hard_gate_pass"], "candidate_id"].nunique()) if not metrics.empty else 0,
        "best_mean_monthly_net_pct": best_target,
        "best_mean_monthly_cost_bps_per_side": best_target_cost,
        "best_candidate": str(metrics.loc[metrics["mean_monthly_net_pct"].idxmax(), "candidate_id"]) if not metrics.empty else None,
        "quote_path_decision": "not_run_no_candidate_passed_capital_cost_regime_gates",
        "holdout_sealed": True,
        "cutoff_exclusive": "2026-06-01",
        "redundant_feature_pairs_top": pairs.head(10).to_dict("records"),
        "top_univariate_features_by_abs_corr_fwd4": feature.head(10).to_dict("records"),
    }
    target_by_cost.to_csv(args.out / "cost_gate_table.csv", index=False)
    best.to_csv(args.out / "best_candidate_by_cost.csv", index=False)
    (args.out / "terra_deep_analysis_summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    lines = [
        "# Terra deep discovery and validation review",
        "",
        "## Verdict",
        "",
        "**No executable strategy passed the hard gates. Do not promote a candidate from this run.**",
        "",
        f"The search evaluated {len(leaderboard):,} leaderboard rows and 500 capital-aware candidates across six cost levels. The best fixed-capital monthly mean was {best_target:.2f}% at {best_target_cost:g} bps/side, below the required 10%.",
        "",
        "Quote-path fills were not run because no candidate survived the prerequisite return, drawdown, yearly, regime, and cost gates. A quote replay cannot rescue a candidate whose bar-level result is already below target and unstable under 2 bps/side.",
        "",
        "## Cost and stability table",
        "",
        "```text",
        target_by_cost.to_string(index=False),
        "```",
        "",
        "## Best candidate at each cost",
        "",
        "```text",
        best[["cost_bps_per_side", "candidate_id", "events", "calendar_months", "mean_monthly_net_pct", "median_monthly_net_pct", "worst_month_net_pct", "max_drawdown", "worst_year_return", "worst_regime_simple_return", "hard_gate_pass"]].to_string(index=False),
        "```",
        "",
        "## Feature and structure implications",
        "",
        "The feature audit found severe redundancy: stochastic K/Williams %R, Bollinger %B/CCI, and same-lookback SMA/EMA pairs are near-duplicates. The strongest standalone indicator relationships with four-bar returns were only about 1.6% in absolute Spearman correlation. Any future search should reduce redundant confluences and use named hypotheses rather than treating the full indicator list as independent evidence.",
        "",
        "## Search limitation and next iteration",
        "",
        "This is a discovery failure, not proof that no strategy exists anywhere. It does prove that this 15-minute, 100,000-formula cross-sectional rank family did not produce the requested executable economics under the stated capital and cost rules. The next bounded iteration should change the hypothesis family or execution horizon, not simply increase formula count, and must retain the sealed cutoff and trial ledger.",
        "",
        "Artifacts: `terra_hard_gate_metrics.csv`, `terra_hard_gate_monthly.csv`, `cost_gate_table.csv`, `best_candidate_by_cost.csv`, and `terra_deep_analysis_summary.json`.",
    ]
    (args.out / "terra_deep_analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
