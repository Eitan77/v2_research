# CAM-0620 rule checklist

## Launch

- [x] Recover and visually verify paper section 4.1.2.
- [x] Freeze PLAN.yaml and RUN-0001 before execution.
- [x] Verify schemas, calendars, point-in-time universes, and source availability.
- [x] Save provenance hashes and confirm maximum loaded date <= 2026-04-30 with zero holdout rows.

## Baseline

- [x] Implement the source-faithful rule without same-bar leakage.
- [x] Run every applicable declared universe and report inapplicable universes concretely.
- [x] Reconcile commands, defaults, variant counts, outputs, and attrition.
- [x] Report fixed-base PnL, monthly/yearly path, drawdown/recovery, activity, turnover, costs, legs, symbols, and concentration.

## Adaptation

- [x] Diagnose alpha, execution, and portfolio construction separately.
- [x] Freeze each meaningful modification before execution.
- [x] Test mechanism-consistent horizons, entries, exits, filters, sizing, risk, and universes.
- [x] Test causal selectability of profitable subsets; never select full-sample winners as prospective.
- [x] Run chronological robustness and parameter-neighborhood checks.

## Execution and conclusion

- [x] Quote replay every economically material survivor profitable at -1/0/1/2 bps.
- [x] Apply realistic short stop, emergency exit, and forced-close evidence where applicable.
- [x] Complete the mandatory conclusion audit before promotion, pivot, or retirement.
- [x] Update RESULTS.yaml, REVIEW.md, WORKLOG.jsonl, research/LEDGER.md, and reusable KNOWLEDGE.md.
- [x] Keep the sealed holdout untouched.


## Completion evidence

- Final decision: `retired_quote_execution_failure`.
- Evidence: `RESULTS.yaml`, `REVIEW.md`, and `artifacts/RUN-0001` through applicable final runs.
- Quote gate was completed or documented inapplicable.
- Sealed holdout remained untouched.
