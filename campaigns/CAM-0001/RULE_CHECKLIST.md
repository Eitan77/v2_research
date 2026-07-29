# CAM-0001 Governing Rule Checklist

This checklist remains active until a checkpoint decision is complete. Evidence
paths are filled as work proceeds. `N/A` requires a concrete reason.

## Launch gates

- [x] Read `AGENTS.md` completely.
- [x] Read every file under `research/` completely.
- [x] Read the complete `campaigns/_TEMPLATE/` tree, including empty files.
- [x] Confirm clean-room rule: no deleted campaign, candidate, parameter,
  ranking, result, or conclusion will be inspected or reused.
- [x] Confirm scope: writes stay inside V2; outside reads are limited to declared
  data paths and copied in-workspace reference code.
- [x] Inventory catalog objects through metadata only; no market rows viewed.
- [x] Define one coherent hypothesis, recent-profile interpretation, adaptation
  boundary, and new-campaign boundary.
- [x] Freeze `PLAN.yaml` and verify its canonical SHA-256.
- [x] Add the campaign-level ledger row.
- [x] Record code/data provenance and fingerprints.
- [x] Verify required schemas, timestamp semantics, adjustments, duplicates,
  missingness, session coverage, and point-in-time eligibility.
- [x] Hard-filter every market-data query through `2026-04-30`.
- [x] Prove the loaded frame's maximum date is `<= 2026-04-30` and its loaded
  holdout-row count is zero.
- [x] Freeze the exact RUN-0001 eligible universe after availability-only
  readiness checks, with symbol/date/row attrition reported.
- [x] Pass semantic fixtures for signal availability, entry/exit timing,
  fixed-base additive P&L, costs, and running-peak drawdown.
- [x] Reconcile frozen RUN-0001 record, command, resolved defaults, executed
  variant count, and output paths before interpreting.

## Run gates

- [x] Pre-register each meaningful run with parent, change, observed question,
  mechanism-based reason, expected effect, full configuration, and short rules.
- [x] Preserve completed configurations/reasons and all failed runs.
- [x] Fail fast on any test, schema, readiness, causal-timing, or holdout error.
- [x] Measure date, symbol, event, and row attrition for every new field, join,
  filter, indicator, or completeness rule.
- [x] Separate alpha, execution, and portfolio-construction diagnoses.
- [x] Use fixed capital 1.0, noncompounded sizing, additive net P&L, equity
  `1 + cumulative P&L`, and running-peak-relative drawdown.
- [x] Report utilization, maximum gross exposure, time in market, turnover,
  P&L per turnover, embedded leverage, and base/adverse/severe costs.
- [x] Use only causal completed-bar information and next-actionable fills.
- [x] Use realistic bar fills first and SIP quotes only for a close, strong, or
  execution-questionable candidate with process-justified latency.
- [x] Reconcile every meaningful run before interpretation; append factual
  result/decision to its YAML and one concise JSONL worklog line.

## Investigation completeness gates

- [x] Verify the internally specified baseline exactly; external source fidelity
  is N/A because no external strategy is claimed. Any later outside source must
  be read to recover signal, universe, formation/skip, timing, entry, exit,
  weighting, controls, mechanism, and falsification conditions.
- [x] Examine mechanism-relevant timeframes, holding periods, entries, exits,
  confirmations, broad thresholds, indicators, sizing, risk, and execution.
- [x] Examine appropriate fixed funds, causal eligibility/rankings, portfolio
  breadth, cash state, and mechanism-consistent underlying or hybrid expressions.
- [x] Diagnose long-only alpha; direct shorts are outside the starting strategy.
  Any later direct short must be intraday with a predefined stop/emergency exit,
  realistic adverse execution, and forced pre-close liquidation.
- [x] Analyze full and recent performance by month, year, chronological fold,
  symbol, episode, signal state, and portfolio component.
- [x] Analyze contribution, sample size, hit rate, independent-period stability,
  top-contributor share, leave-one-out results, common causal characteristics,
  and prospective selectability.
- [x] Examine 12-, 15-, and 18-month views and any causal onset without selecting
  a start date for maximum historical return.
- [x] Examine drawdown shape/duration, full recovery time, tails, gaps, beta,
  factor/market exposure, capacity/liquidity, delay, costs, and parameter
  neighborhoods.
- [x] Define and test causal activation, decay monitoring, and prospective
  suspension/retirement logic.
- [x] Quantify research breadth, dependence/effective events, adaptation burden,
  and remaining independent evidence.
- [x] Continue mechanism-consistent tests while a principled discriminating
  question remains; do not use leverage, narrow hindsight filters, or precise
  parameters to rescue weak alpha.

## Conclusion audit

- [x] All applicable governing requirements have evidence or concrete N/A.
- [x] Starting implementation/source fidelity is verified.
- [x] Relevant horizons, universes, assets, confirmations, filters, indicators,
  sizing, risk, and execution choices were investigated.
- [x] Symbols, episodes, legs, concentration, leave-one-out behavior, and causal
  profitable-subset selection were audited.
- [x] Every adaptation addressed a diagnosed weakness and its selection burden
  is disclosed.
- [x] No careful skeptical quant's obvious mechanism-consistent experiment
  remains unresolved.
- [x] Researcher, skeptic, and portfolio-engineer perspectives are recorded.
- [x] Promotion/retirement/pivot claim matches the evidence and never calls the
  broader Strategy A profile exhausted from one campaign.
- [x] `RESULTS.yaml`, `REVIEW.md`, `WORKLOG.jsonl`, `research/LEDGER.md`, and
  when warranted `research/KNOWLEDGE.md`/`research/CURRENT.md` are updated.
- [x] Holdout remains sealed; no data on or after `2026-05-01` was accessed.

## Completion evidence

All gates were audited in `artifacts/CONCLUSION_AUDIT.md` after RUN-0011. Full SIP NBBO replay is complete as a documented inapplicable result for interpretation (0/45 local coverage and no provider credentials); it blocks promotion and remains the next external-evidence task. The broader Strategy A profile is not exhausted.
