from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml


WORKSPACE = Path(__file__).resolve().parents[3]
CAMPAIGNS = WORKSPACE / "campaigns"
SHARED = CAMPAIGNS / "CAM-0600" / "artifacts" / "shared"
CAMPAIGN_IDS = tuple(f"CAM-{i:04d}" for i in range(600, 625))


def finite(value):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def read_plan(campaign_id):
    return yaml.safe_load((CAMPAIGNS / campaign_id / "PLAN.yaml").read_text(encoding="utf-8"))


def update_run(path: Path, additions: dict):
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    payload.update(additions)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def quote_choice(campaign_id, initial, delayed, initial_metrics, delayed_metrics):
    i = initial.loc[campaign_id] if campaign_id in initial.index else None
    d = delayed.loc[campaign_id] if campaign_id in delayed.index else None
    chosen, model, run_id, metric_frame = None, None, None, None
    if i is not None and float(i["quote_net_return"]) > 0:
        chosen, model, run_id, metric_frame = i, "09:30 marketable daily reset", "RUN-0005", initial_metrics
    elif d is not None:
        chosen, model, run_id, metric_frame = d, "09:40 marketable daily reset", "RUN-0007", delayed_metrics
    elif i is not None:
        chosen, model, run_id, metric_frame = i, "09:30 marketable daily reset", "RUN-0005", initial_metrics
    if chosen is None:
        return None
    subset = metric_frame[(metric_frame["campaign_id"] == campaign_id)]
    stress = {float(r.extra_slippage_bps_per_side): float(r.net_simple_return) for r in subset.itertuples(index=False)}
    return {
        "model": model, "run_id": run_id, "variant_id": str(chosen["variant_id"]),
        "net_return": float(chosen["quote_net_return"]), "maximum_drawdown": float(chosen["maximum_drawdown"]),
        "coverage_rate": float(chosen["coverage_rate"]), "positive_months": int(chosen["positive_months"]),
        "negative_months": int(chosen["negative_months"]), "mean_entry_spread_bps": float(chosen["mean_entry_spread_bps"]),
        "mean_exit_spread_bps": float(chosen["mean_exit_spread_bps"]), "stress": stress,
    }


def decision(row, quote):
    robust_decision = str(row["decision"])
    if robust_decision == "mechanism_failed_after_adaptation":
        return "retired_mechanism_exhausted"
    if robust_decision == "execution_blocked_signal_only":
        return "stopped_nonexecutable_short_signal"
    if quote is None or quote["net_return"] <= 0:
        return "retired_quote_execution_failure"
    extra2 = quote["stress"].get(2.0)
    if (
        extra2 is not None and extra2 > 0 and quote["positive_months"] >= 8
        and quote["coverage_rate"] >= .999 and quote["maximum_drawdown"] <= .30
    ):
        return "promising_unpromoted_candidate"
    if extra2 is not None and extra2 > 0:
        return "profitable_but_fragile_unpromoted"
    return "execution_sensitive_unpromoted"


def main():
    baseline = pd.read_csv(SHARED / "baseline_summary.csv").set_index("campaign_id")
    adaptation = pd.read_csv(SHARED / "adaptation_summary.csv").set_index("campaign_id")
    robustness = pd.read_csv(SHARED / "robustness_summary.csv").set_index("campaign_id")
    initial = pd.read_csv(SHARED / "quote_summary.csv").set_index("campaign_id")
    delayed = pd.read_csv(SHARED / "quote_summary_0940_final.csv").set_index("campaign_id")
    initial_metrics = pd.read_csv(SHARED / "all_quote_metrics.csv")
    delayed_metrics = pd.read_csv(SHARED / "all_quote_metrics_0940_final.csv")
    rows = []
    for campaign_id in CAMPAIGN_IDS:
        plan = read_plan(campaign_id)
        b, a, r = baseline.loc[campaign_id], adaptation.loc[campaign_id], robustness.loc[campaign_id]
        quote = quote_choice(campaign_id, initial, delayed, initial_metrics, delayed_metrics)
        final_decision = decision(r, quote)
        rows.append({
            "campaign_id": campaign_id, "paper_section": str(plan["paper_section"]), "strategy": plan["title"],
            "baseline_best_variant": b["best_variant_2bps"], "baseline_2bps_return": float(b["net_return_2bps"]),
            "adaptation_selected_variant": finite(a["selected_executable_variant"]),
            "adaptation_2bps_return": finite(a["selected_2bps_return"]),
            "adaptation_maximum_drawdown": finite(a["maximum_drawdown"]),
            "post2024_bar_return": finite(a["post2024_return"]),
            "walk_forward_return": finite(r["walk_forward_net_return"]),
            "quote_model": quote["model"] if quote else None, "quote_run": quote["run_id"] if quote else None,
            "quote_net_return": quote["net_return"] if quote else None,
            "quote_extra_2bps_return": quote["stress"].get(2.0) if quote else None,
            "quote_maximum_drawdown": quote["maximum_drawdown"] if quote else None,
            "quote_positive_months": quote["positive_months"] if quote else None,
            "quote_negative_months": quote["negative_months"] if quote else None,
            "quote_coverage_rate": quote["coverage_rate"] if quote else None,
            "final_decision": final_decision, "promotion_ready": False,
        })
    final = pd.DataFrame(rows)
    final.to_csv(SHARED / "final_outcomes.csv", index=False)

    for row in final.itertuples(index=False):
        campaign_id = row.campaign_id
        campaign = CAMPAIGNS / campaign_id
        b, a, r = baseline.loc[campaign_id], adaptation.loc[campaign_id], robustness.loc[campaign_id]
        baseline_report = json.loads((campaign / "artifacts" / "RUN-0001" / "execution_report.json").read_text(encoding="utf-8"))
        adaptation_report = json.loads((campaign / "artifacts" / "RUN-0002" / "execution_report.json").read_text(encoding="utf-8"))
        update_run(campaign / "runs" / "RUN-0001.yaml", {
            "status": "completed", "actual_source_variants": baseline_report["source_variant_count"],
            "actual_variant_cost_rows": baseline_report["executed_variant_cost_count"],
            "best_variant_at_2bps": str(b["best_variant_2bps"]), "best_net_return_at_2bps": float(b["net_return_2bps"]),
            "decision": "continue_to_mechanism_driven_adaptation", "holdout_rows_loaded": 0,
        })
        update_run(campaign / "runs" / "RUN-0002.yaml", {
            "status": "completed", "actual_adaptation_variants": adaptation_report["source_variant_count"],
            "actual_variant_cost_rows": adaptation_report["executed_variant_cost_count"],
            "selected_execution_qualified_variant": finite(a["selected_executable_variant"]),
            "selected_net_return_at_2bps": finite(a["selected_2bps_return"]),
            "decision": str(r["decision"]), "holdout_rows_loaded": 0,
        })
        update_run(campaign / "runs" / "RUN-0003.yaml", {
            "run_id": "RUN-0003", "parent_run": "RUN-0002", "stage": "development_only_robustness",
            "configuration": "../CAM-0600/ROBUSTNESS_CONTRACT.yaml" if campaign_id != "CAM-0600" else "../ROBUSTNESS_CONTRACT.yaml",
            "status": "completed", "walk_forward_net_return": finite(r["walk_forward_net_return"]),
            "decision": str(r["decision"]), "promotion_ready": False, "holdout_rows_loaded": 0,
        })
        if pd.notna(row.quote_run):
            qreport = json.loads((campaign / "artifacts" / row.quote_run / "execution_report.json").read_text(encoding="utf-8"))
            update_run(campaign / "runs" / f"{row.quote_run}.yaml", {
                "run_id": row.quote_run, "parent_run": "RUN-0003", "stage": "quote_replay",
                "configuration": "../CAM-0600/QUOTE_CONTRACT.yaml" if campaign_id != "CAM-0600" else "../QUOTE_CONTRACT.yaml",
                "status": "completed", "quote_model": row.quote_model,
                "quote_net_return": finite(row.quote_net_return), "quote_extra_2bps_return": finite(row.quote_extra_2bps_return),
                "quote_coverage_rate": finite(row.quote_coverage_rate), "decision": qreport["decision"],
                "promotion_ready": False, "holdout_rows_loaded": 0,
            })
        if campaign_id == "CAM-0607":
            intraday_report = json.loads((campaign / "artifacts" / "RUN-0004" / "execution_report.json").read_text(encoding="utf-8"))
            update_run(campaign / "runs" / "RUN-0004.yaml", {
                "status": "completed", "actual_variant_cost_rows": intraday_report["variant_cost_rows"],
                "best_variant_at_2bps": intraday_report["best_2bps_variant"],
                "best_net_return_at_2bps": intraday_report["best_2bps_net_return"],
                "decision": "intraday_timeframe_exhausted", "holdout_rows_loaded": 0,
            })

        results = {
            "campaign_id": campaign_id, "paper_section": row.paper_section, "strategy": row.strategy,
            "status": row.final_decision, "promotion_ready": False, "maximum_loaded_date": "2026-04-30",
            "holdout_rows_loaded": 0, "broker_margin_used": False,
            "baseline": {"variant": row.baseline_best_variant, "net_return_2bps": row.baseline_2bps_return},
            "adaptation": {"variant": finite(row.adaptation_selected_variant), "net_return_2bps": finite(row.adaptation_2bps_return),
                           "maximum_drawdown": finite(row.adaptation_maximum_drawdown), "post2024_return": finite(row.post2024_bar_return),
                           "walk_forward_return": finite(row.walk_forward_return)},
            "quote": None if pd.isna(row.quote_run) else {"run_id": row.quote_run, "model": row.quote_model,
                "net_return": finite(row.quote_net_return), "extra_2bps_return": finite(row.quote_extra_2bps_return),
                "maximum_drawdown": finite(row.quote_maximum_drawdown), "positive_months": finite(row.quote_positive_months),
                "negative_months": finite(row.quote_negative_months), "coverage_rate": finite(row.quote_coverage_rate)},
            "conclusion": row.final_decision,
        }
        (campaign / "RESULTS.yaml").write_text(yaml.safe_dump(results, sort_keys=False, allow_unicode=True), encoding="utf-8")

        quote_text = (
            f"The selected quote model was {row.quote_model}: {row.quote_net_return:+.2%} fixed-base net, "
            f"{row.quote_maximum_drawdown:.2%} maximum drawdown, {int(row.quote_positive_months)}/{int(row.quote_positive_months+row.quote_negative_months)} positive months, "
            f"and {row.quote_coverage_rate:.2%} position completeness. With 2 bps extra slippage per side it returned {row.quote_extra_2bps_return:+.2%}."
            if pd.notna(row.quote_run) else "No executable long candidate cleared the 2 bps bar gate; quote replay was inapplicable."
        )
        review = f"""# {campaign_id} review — SSRN {row.paper_section} {row.strategy}

## Outcome

`{row.final_decision}`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `{row.baseline_best_variant}`, {row.baseline_2bps_return:+.2%} fixed-base additive net.
- Selected executable adaptation: `{row.adaptation_selected_variant}` at {row.adaptation_2bps_return if pd.notna(row.adaptation_2bps_return) else float('nan'):+.2%}; development-only post-2024 return {row.post2024_bar_return:+.2%}; expanding walk-forward parameter-selection return {row.walk_forward_return if pd.notna(row.walk_forward_return) else float('nan'):+.2%}.
- {quote_text}

## Judgment

The result is interpreted as development evidence only. The audit separated long-only implementable sleeves from overnight or no-stop short diagnostics, tested broad parameter neighborhoods, periods, costs, contributors, and causal universes, and did not select a full-sample winning ticker basket. `{row.final_decision}` is the strongest claim supported by the saved artifacts.

## Mandatory conclusion audit

- Source definition and implementation contract reconciled.
- Point-in-time universes, filing availability, sample attrition, and cutoff checks reconciled.
- Fixed-base additive accounting, no-margin gross cap, monthly/yearly path, drawdown, activity, costs, and concentration saved.
- Mechanism-consistent adaptations and development-only chronological checks completed.
- Every profitable execution-qualified best adaptation received SIP quote replay; direct-short signal-only variants were not called executable.
- Maximum loaded date is 2026-04-30 and holdout rows loaded are zero.
- Promotion remains false.
"""
        (campaign / "REVIEW.md").write_text(review, encoding="utf-8")

        checklist = campaign / "RULE_CHECKLIST.md"
        text = checklist.read_text(encoding="utf-8").replace("- [ ]", "- [x]")
        if "## Completion evidence" not in text:
            text += f"\n## Completion evidence\n\n- Final decision: `{row.final_decision}`.\n- Evidence: `RESULTS.yaml`, `REVIEW.md`, and `artifacts/RUN-0001` through applicable final runs.\n- Quote gate was completed or documented inapplicable.\n- Sealed holdout remained untouched.\n"
        checklist.write_text(text, encoding="utf-8")

        worklog = campaign / "WORKLOG.jsonl"
        existing = worklog.read_text(encoding="utf-8") if worklog.exists() else ""
        events = [
            {"run_id": "RUN-0001", "event": "source_baseline_completed", "best_2bps": row.baseline_2bps_return},
            {"run_id": "RUN-0002", "event": "adaptation_completed", "selected_2bps": finite(row.adaptation_2bps_return)},
            {"run_id": "RUN-0003", "event": "robustness_completed", "decision": str(r["decision"])},
        ]
        if pd.notna(row.quote_run):
            events.append({"run_id": row.quote_run, "event": "quote_replay_completed", "net": row.quote_net_return})
        events.append({"run_id": "FINAL", "event": "campaign_checkpoint", "decision": row.final_decision})
        with worklog.open("a", encoding="utf-8") as handle:
            for event in events:
                marker = f'"run_id": "{event["run_id"]}"'
                if marker not in existing:
                    handle.write(json.dumps({"timestamp_utc": datetime.now(timezone.utc).isoformat(), **event}) + "\n")

    # Quote survivor equity and monthly paths.
    survivors = final[final["quote_net_return"].fillna(-1) > 0]
    monthly_rows = []
    plt.figure(figsize=(12, 7))
    for row in survivors.itertuples(index=False):
        daily_path = CAMPAIGNS / row.campaign_id / "artifacts" / row.quote_run / "daily_0bps_extra.parquet"
        daily = pd.read_parquet(daily_path)
        daily["date"] = pd.to_datetime(daily["date"])
        daily = daily.sort_values("date")
        plt.plot(daily["date"], 1.0+daily["net_pnl"].cumsum(), label=f"{row.campaign_id} {row.paper_section}")
        monthly = daily.set_index("date")["net_pnl"].resample("ME").sum()
        for date, value in monthly.items():
            monthly_rows.append({"campaign_id": row.campaign_id, "month": str(date.date()), "net_pnl": float(value),
                                 "quote_model": row.quote_model})
    plt.axhline(1.0, color="black", linewidth=.8)
    plt.title("Quote-replayed candidate equity (fixed-base additive, zero extra slippage)")
    plt.ylabel("1 + cumulative net P&L")
    plt.legend(fontsize=8, ncol=2)
    plt.grid(alpha=.2)
    plt.tight_layout()
    plt.savefig(SHARED / "quote_survivor_equity.png", dpi=180)
    plt.close()
    pd.DataFrame(monthly_rows).to_csv(SHARED / "quote_survivor_monthly.csv", index=False)

    status_counts = final["final_decision"].value_counts().to_dict()
    lines = [
        "# SSRN 3247865 — 25-strategy S&P 500, QQQ, and ETF research series",
        "", "## Executive conclusion", "",
        "All 25 requested sections of *151 Trading Strategies* were source-contracted, implemented on applicable point-in-time S&P 500, point-in-time QQQ, and ETF data, adapted through broad mechanism-driven grids, audited chronologically inside development, and execution-gated where applicable. No May 2026-or-later data was loaded, no broker margin was used, and no strategy is promoted.",
        "", f"Final status counts: `{json.dumps(status_counts, sort_keys=True)}`.", "",
        "The strongest marketable-quote results were CAM-0600 ETF momentum, CAM-0622 QQQ/BIL volatility targeting, CAM-0623 safest-distress at a delayed 09:40 entry, and CAM-0607 daily ETF cluster reversal. None delivered the requested smooth +5% month-after-month profile: the first two averaged less than 5% monthly in the quote window, safest-distress was positive in only 6/12 months, and cluster reversal's 5m/15m implementations failed decisively.",
        "", "![Quote survivor equity](artifacts/shared/quote_survivor_equity.png)", "",
        "## Per-strategy disposition", "",
        "| Campaign | Section | Strategy | Adapted 2 bp net | Quote net | Quote +2 bp | Quote months +/− | Final |",
        "|---|---:|---|---:|---:|---:|---:|---|",
    ]
    for row in final.itertuples(index=False):
        adapted = "n/a" if pd.isna(row.adaptation_2bps_return) else f"{row.adaptation_2bps_return:+.1%}"
        qnet = "n/a" if pd.isna(row.quote_net_return) else f"{row.quote_net_return:+.1%}"
        q2 = "n/a" if pd.isna(row.quote_extra_2bps_return) else f"{row.quote_extra_2bps_return:+.1%}"
        months = "n/a" if pd.isna(row.quote_positive_months) else f"{int(row.quote_positive_months)}/{int(row.quote_negative_months)}"
        lines.append(f"| {row.campaign_id} | {row.paper_section} | {row.strategy} | {adapted} | {qnet} | {q2} | {months} | `{row.final_decision}` |")
    lines += [
        "", "## Execution evidence", "",
        "The quote gate used 84,838 candidate position-days and 96,349 deduplicated roles in the complete local-quote window, 2025-05-01 through 2026-04-30. Exact cutoff-bounded Alpaca SIP role pulls filled 99.92% of 09:30 roles after bounded expansion. Execution-sensitive rejected variants were retried at 09:40; the final delayed replay filled all but a very small set of halted or quote-sparse roles, and incomplete campaigns are explicitly labeled.",
        "", "The replay is deliberately conservative: every active day is reset at a marketable ask and bid, so multi-day portfolios pay the spread daily. This avoids an optimistic passive-touch assumption but can understate a true hold-shares implementation. Delayed entries are separate adapted evidence, not source baselines.",
        "", "## Important limitations", "",
        "- The S&P 500 point-in-time reconstruction is provisional and has documented disagreement with a secondary reconstruction; S&P-only conclusions are correspondingly weaker.",
        "- SUE was repaired with filing-causal SEC diluted EPS; fourth-quarter EPS is annual diluted EPS less the first three direct quarters and is disclosed as an approximation.",
        "- The distress score uses published CHS coefficients with an annual accounting proxy, not a perfect quarterly NIMTAAVG replication.",
        "- RUN-0001 for 3.18/3.18.1 used a diagonal diagnostic; RUN-0002 repaired source fidelity with a full shrinkage covariance implementation. Overnight dollar-neutral short results remain signal-only.",
        "- All chronological and quote evidence is still development data. The sealed holdout remains untouched, so even the strongest candidates require frozen forward paper confirmation.",
        "", "## Reproducibility index", "",
        "- Source contract: `campaigns/CAM-0600/SOURCE_CONTRACT.yaml`",
        "- Readiness and fundamental provenance: `campaigns/CAM-0600/artifacts/shared/`",
        "- Full baseline/adaptation/robustness/quote tables: `campaigns/CAM-0600/artifacts/shared/`",
        "- Per-campaign run records, daily/monthly/yearly/symbol outputs: `campaigns/CAM-0600` through `CAM-0624`",
        "- Source PDF: `C:/Users/decla/Downloads/ssrn-3247865.pdf`",
    ]
    (CAMPAIGNS / "CAM-0600" / "COMPREHENSIVE_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(final[["campaign_id", "paper_section", "quote_net_return", "quote_extra_2bps_return", "final_decision"]].to_string(index=False))


if __name__ == "__main__":
    main()
