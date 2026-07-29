# CAM-0001 Mandatory Conclusion Audit

Completed 2026-07-28 after RUN-0011 and before the campaign decision.

## Integrity and source fidelity

- PASS — `PLAN.yaml` was frozen before testing; canonical SHA-256 excluding its
  hash line is `ba5f5d6a7efca432369b943a294633aa89b9472e931e4dca68e1a7c98b1a27bd`.
- PASS — The starting implementation was internally specified, so external
  source replication is inapplicable. RUN-0001 exactly reconciled the frozen
  signal, universe, timing, weighting, costs, and output contract.
- PASS — All market queries were hard-filtered through 2026-04-30; every run
  contract reports zero loaded holdout rows. May 1, 2026 onward remained sealed.
- PASS WITH INCIDENT — A recursive filesystem metadata enumeration during
  provenance timed out before a cutoff proof was recorded. No price, return,
  candidate, or displayed holdout filename informed research. The incident is
  preserved in `research/HOLDOUT_ACCESS.md`.
- PASS WITH REPAIR — Ten split-adjusted daily gaps were discovered after the
  initial readiness interpretation. The result was invalidated before strategy
  use, reconstructed from cutoff-bounded raw/split bracketing factors, and
  revalidated. The RUN-0010 split-minute query also failed fast; raw SIP minute
  bars were then causally reconstructed with same-date daily split factors.
- PASS WITH REPAIR — The final artifact audit caught one mistyped RUN-0005 hash
  and one stale RUN-0007 CSV hash. Both records were corrected to the actual
  immutable files; all 42 recorded artifact hashes now verify.

## Coverage of the strategy question

- PASS — Timeframes: 3/5/10/20/60-session formation, 1/3/5/7/10-session holds,
  next-session and two-session entries, causal next-open invalidation exits,
  and 10%-20% gap-aware stops.
- PASS — Confirmations and filters: QQQ SMA20/50/100/200, above/rising state,
  QQQ/fund participation, volatility, trend efficiency, and combined filters.
- PASS — Assets/universes: TQQQ/SOXL, unleveraged QQQ/SMH, long SQQQ/SOXS, and
  causal bull/bear switching. No direct shorts were used.
- PASS — Portfolio construction: top one/two, equal, inverse-volatility, SOXL
  cap, QQQ volatility scaling, full investment, and cash when ineligible.
- PASS — Execution: 5/10/20 bps per side, one full extra-session delay, SIP
  minute-bar 0/1/5/30-minute fills, and adverse entry-high/exit-low bounds.
  Full SIP NBBO replay is explicitly unresolved because coverage is 0/45 and
  no provider credentials were present; this blocks promotion, not the audit.
- PASS — Alpha, product leverage, execution, and portfolio construction were
  diagnosed separately. The unleveraged expression preserved the path at
  roughly one-third magnitude; active market beta was 3.21.

## Stability, concentration, and selection

- PASS — Complete monthly, calendar-year, half-year, rolling-window, drawdown,
  recovery, utilization, turnover, and beta paths are preserved.
- PASS — TQQQ/SOXL full-history contribution was balanced, but recent SOXL
  dependence and decision concentration were disclosed. Among 19 recent
  decisions, the top one supplied 25.6% and top five 87.0%; leave-best and
  leave-worst net results are recorded.
- PASS — The profitable subset was tested through causal market/fund states,
  unleveraged assets, sizing, and universe legs. A full-sample winner basket
  was not presented as prospective validation.
- PASS — The selection audit counts 171 meaningful variants before RUN-0008,
  evaluates 77 rolling 12-month endpoints, and labels the candidate adapted.
  The final 12-month endpoint was 98.7th percentile; bootstrap results do not
  correct the search.
- PASS — Twelve, fifteen, and eighteen months were assessed without a favorable
  onset choice. Fifteen months remains representative because no causal
  observable justified the May 2025 start.

## Adaptation and remaining questions

- PASS — Each run named its parent, weakness/question, mechanism reason,
  expected effect, frozen configuration, factual result, and decision. All
  eleven run records and failures are preserved.
- PASS — Mechanism-consistent attempts addressed consistency, recovery,
  concentration, market-state selection, sizing, bearish states, costs, and
  execution. Failed first expressions were diagnosed rather than abandoned.
- PASS — Causal three/six-month decay monitors were tested and rejected as
  historical entry filters because they worsened sparsity/recovery. A
  prospective suspension contract is frozen instead.
- PASS — No obvious additional in-sample variation remains that is both
  mechanistically distinct and likely to provide independent evidence.
  Additional thresholds, stops, or filters would mainly seek a better chart.
  The unresolved actions—complete historical ETF NBBO and prospective paper
  observations—require new evidence, not parameter adaptation.

## Conclusion

The researcher, skeptic, and portfolio-engineer views agree: the continuation
state is credible and worth freezing for independent confirmation, but it is
not an exceptional A-type strategy and is not promotion-ready. Development
ends as `development_complete_forward_confirmation_required`. This conclusion
does not exhaust the broader Strategy A profile.
