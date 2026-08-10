from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0600" / "artifacts" / "RUN-0044"


def dd(pnl: pd.Series) -> float:
    equity = 1.0 + pnl.cumsum()
    return float(((equity.cummax() - equity) / equity.cummax()).max()) if len(pnl) else 0.0


def daily(candidate: str) -> pd.Series:
    frame = pd.read_parquet(OUT / f"daily_{candidate}_2bps.parquet")
    return pd.Series(frame.net_pnl.to_numpy(float), index=pd.to_datetime(frame.date))


def stats(pnl: pd.Series, prefix: str) -> dict:
    monthly = pnl.groupby(pnl.index.to_period("M")).sum()
    return {
        f"{prefix}_net": float(pnl.sum()),
        f"{prefix}_drawdown": dd(pnl),
        f"{prefix}_positive_months": int((monthly > 1e-12).sum()),
        f"{prefix}_negative_months": int((monthly < -1e-12).sum()),
        f"{prefix}_worst_month": float(monthly.min()) if len(monthly) else 0.0,
    }


def main() -> None:
    quotes = pd.read_csv(OUT / "quote_metrics.csv")
    q2 = quotes.loc[quotes.extra_adverse_bps_per_side.eq(2)].set_index("candidate")
    q10 = quotes.loc[quotes.extra_adverse_bps_per_side.eq(10)].set_index("candidate")
    selected = pd.read_csv(OUT / "training_selected_configs.csv")
    selected_rows = []
    for row in selected.itertuples(index=False):
        chosen = f"{row.family}_top{int(row.top_k)}_f{int(row.formation)}_s{int(row.skip)}"
        baseline = f"{row.family}_top{int(row.top_k)}_f126_s21"
        a, b = q2.loc[chosen], q2.loc[baseline]
        selected_rows.append({
            "family": row.family, "top_k": int(row.top_k), "formation": int(row.formation), "skip": int(row.skip),
            "selected_quote_2bps": a.net_simple_return, "baseline_quote_2bps": b.net_simple_return,
            "full_quote_improvement": a.net_simple_return - b.net_simple_return,
            "recent12_quote_improvement": a.recent12_net_simple_return - b.recent12_net_simple_return,
            "drawdown_change": a.maximum_drawdown - b.maximum_drawdown,
            "bar_validation_improvement": row.validation_improvement_vs_baseline,
        })
    selected_comparison = pd.DataFrame(selected_rows)

    decisions = pd.read_csv(OUT / "walkforward_selections.csv")
    wf_rows = []
    for (family, top_k), group in decisions.groupby(["family", "top_k"], sort=True):
        start = pd.Timestamp(f"{int(group.evaluation_year.min())}-01-01")
        wf_name = f"{family}_top{int(top_k)}_walkforward"
        base_name = f"{family}_top{int(top_k)}_f126_s21"
        wp = daily(wf_name).loc[lambda x: x.index >= start]
        bp = daily(base_name).loc[lambda x: x.index >= start]
        rec = {"family": family, "top_k": int(top_k), "evaluation_start": str(start.date())}
        rec.update(stats(wp, "walkforward"))
        rec.update(stats(bp, "baseline"))
        rec["net_improvement"] = rec["walkforward_net"] - rec["baseline_net"]
        rec["drawdown_change"] = rec["walkforward_drawdown"] - rec["baseline_drawdown"]
        wf_rows.append(rec)
    wf_comparison = pd.DataFrame(wf_rows)

    attrition = pd.read_csv(OUT / "sample_attrition.csv").drop_duplicates("family").set_index("family")
    dominant = pd.read_csv(OUT / "posthoc_bar_dominant_configs.csv")
    dominant_rows = []
    for row in dominant.itertuples(index=False):
        name = f"{row.family}_top{int(row.top_k)}_f{int(row.formation)}_s{int(row.skip)}"
        baseline = f"{row.family}_top{int(row.top_k)}_f126_s21"
        a2, b2, a10, b10 = q2.loc[name], q2.loc[baseline], q10.loc[name], q10.loc[baseline]
        validation_start = pd.Timestamp(attrition.loc[row.family, "validation_start_date"])
        av = daily(name).loc[lambda x: x.index >= validation_start].sum()
        bv = daily(baseline).loc[lambda x: x.index >= validation_start].sum()
        rec = {
            "family": row.family, "top_k": int(row.top_k), "formation": int(row.formation), "skip": int(row.skip),
            "quote_2bps": a2.net_simple_return, "baseline_quote_2bps": b2.net_simple_return,
            "full_quote_improvement": a2.net_simple_return - b2.net_simple_return,
            "validation_quote_improvement": float(av - bv),
            "recent12_quote_improvement": a2.recent12_net_simple_return - b2.recent12_net_simple_return,
            "drawdown_change": a2.maximum_drawdown - b2.maximum_drawdown,
            "quote_10bps_improvement": a10.net_simple_return - b10.net_simple_return,
            "top5_share": a2.top5_symbol_positive_share,
            "leave_top5_return": a2.leave_top5_return,
        }
        rec["quote_dominates_baseline"] = bool(
            rec["full_quote_improvement"] > 0
            and rec["validation_quote_improvement"] > 0
            and rec["recent12_quote_improvement"] > 0
            and rec["drawdown_change"] <= 1e-12
            and rec["quote_10bps_improvement"] > 0
        )
        dominant_rows.append(rec)
    dominant_comparison = pd.DataFrame(dominant_rows)
    robust = dominant_comparison.loc[dominant_comparison.quote_dominates_baseline].copy()
    robust_summary = robust.groupby(["family", "top_k"], as_index=False).agg(
        robust_cells=("formation", "size"),
        formations=("formation", lambda x: ",".join(map(str, sorted(set(x))))),
        skips=("skip", lambda x: ",".join(map(str, sorted(set(x))))),
        best_full_quote_improvement=("full_quote_improvement", "max"),
        median_full_quote_improvement=("full_quote_improvement", "median"),
        best_recent12_quote_improvement=("recent12_quote_improvement", "max"),
        best_drawdown_reduction=("drawdown_change", "min"),
    ) if len(robust) else pd.DataFrame()

    selected_comparison.to_csv(OUT / "training_selected_quote_comparison.csv", index=False)
    wf_comparison.to_csv(OUT / "walkforward_quote_comparison.csv", index=False)
    dominant_comparison.to_csv(OUT / "posthoc_dominant_quote_comparison.csv", index=False)
    robust.to_csv(OUT / "quote_robust_improvements.csv", index=False)
    robust_summary.to_csv(OUT / "quote_robust_improvement_summary.csv", index=False)
    report = {
        "status": "completed",
        "quote_candidates": int(q2.index.nunique()),
        "minimum_role_coverage": float(q2.role_coverage.min()),
        "training_selected_full_improvements": int((selected_comparison.full_quote_improvement > 0).sum()),
        "training_selected_recent_improvements": int((selected_comparison.recent12_quote_improvement > 0).sum()),
        "training_selected_validation_improvements": int((selected_comparison.bar_validation_improvement > 0).sum()),
        "walkforward_matched_improvements": int((wf_comparison.net_improvement > 0).sum()),
        "posthoc_bar_dominant_cells": len(dominant_comparison),
        "posthoc_quote_robust_cells": len(robust),
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
    }
    (OUT / "analysis_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("\nROBUST QUOTE REGIONS")
    print(robust_summary.to_string(index=False) if len(robust_summary) else "none")
    print("\nWALK-FORWARD GAINS")
    print(wf_comparison.loc[wf_comparison.net_improvement > 0].to_string(index=False))


if __name__ == "__main__":
    main()
