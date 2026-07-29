# CAM-0002 Governing Checklist

## Launch

- [x] Re-read active governance, current direction, data/code declarations, knowledge, and ledger.
- [x] Confirm CAM-0002 is materially different from CAM-0001.
- [x] Recover the primary source's exact universe, event, timing, entry, exit, controls, mechanism, and spread caveat.
- [x] Visually inspect all 27 source pages and preserve the source PDF/hash.
- [x] Freeze PLAN.yaml before readiness or backtesting.
- [x] Verify cutoff-bounded minute/daily schemas, partitions, adjustments, timestamps, sessions, and point-in-time eligibility.
- [x] Prove maximum loaded date <= 2026-04-30 and zero holdout rows.
- [x] Freeze RUN-0001 record and reconcile command/defaults/variant count/outputs.
- [x] Pass fixtures for completed-minute causality, event localization, non-overlap, fixed-base P&L, drawdown, and holdout fail-fast.

## Runs and investigation

- [x] Preserve every meaningful parent/change/reason/expectation/result/decision.
- [x] Measure row, date, symbol, event, and cluster attrition.
- [x] Reproduce the source-faithful long-after-drop component before adapting.
- [x] Diagnose gross alpha, spread/execution, and portfolio construction separately.
- [x] Examine source threshold/horizon neighborhoods, liquidity, volatility, residualization, clustering, and capacity.
- [x] Audit months, symbols, events, clusters, top contributors, leave-one-out, drawdown, recovery, and selection burden.
- [x] Use SIP trades/quotes only after bar screening warrants them.
- [x] Define causal activation, decay monitoring, suspension, and retirement.

## Conclusion

- [x] Complete the eleven-principle audit before any promotion, retirement, pivot, or checkpoint.
- [x] Confirm no obvious mechanism-consistent experiment remains.
- [x] Record researcher, skeptic, and portfolio-engineer views.
- [x] Update RESULTS, REVIEW, WORKLOG, LEDGER, KNOWLEDGE, and CURRENT as applicable.
- [x] Preserve the sealed holdout and do not call Strategy A exhausted from this campaign.

## Completion evidence

All gates were audited in `artifacts/CONCLUSION_AUDIT.md` after RUN-0010.
Activation/decay and longer-history testing are concretely inapplicable because
no recent candidate survived. Quote replay was conserved because the best bar
result was sparse and roughly one-eighth of Strategy A. Six semantic tests
passed and the holdout remained sealed.
