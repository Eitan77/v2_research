# CAM-0616 review — SSRN 3.18.1 Dollar-neutral statistical-arbitrage optimization

## Outcome

`stopped_nonexecutable_short_signal`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `qqq__momentum20__diag_cov60__dollar_neutral`, +15.78% fixed-base additive net.
- Selected executable adaptation: `nan` at +nan%; development-only post-2024 return +7.79%; expanding walk-forward parameter-selection return +nan%.
- No executable long candidate cleared the 2 bps bar gate; quote replay was inapplicable.

## Judgment

The result is interpreted as development evidence only. The audit separated long-only implementable sleeves from overnight or no-stop short diagnostics, tested broad parameter neighborhoods, periods, costs, contributors, and causal universes, and did not select a full-sample winning ticker basket. `stopped_nonexecutable_short_signal` is the strongest claim supported by the saved artifacts.

## Mandatory conclusion audit

- Source definition and implementation contract reconciled.
- Point-in-time universes, filing availability, sample attrition, and cutoff checks reconciled.
- Fixed-base additive accounting, no-margin gross cap, monthly/yearly path, drawdown, activity, costs, and concentration saved.
- Mechanism-consistent adaptations and development-only chronological checks completed.
- Every profitable execution-qualified best adaptation received SIP quote replay; direct-short signal-only variants were not called executable.
- Maximum loaded date is 2026-04-30 and holdout rows loaded are zero.
- Promotion remains false.

## 2026-08-10 deep-development checkpoint

Paper section 3.18.1, **Dollar-neutral optimization**. Source contract: Mean-variance solution subtracts the covariance-weighted intercept component so sum(w)=0; normalize sum(abs(w))=1.

The structured survivor `qqq__fullcov_s50__mom60__positive_top10` earned +131.0% net at 2 bps over its available development history and +33.3% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +33.9% with 7.4% drawdown, 9/3 positive/negative months, and 12.4% of positive P&L from the best five days.

Selection activity covered 92.3% of dates and averaged 10.00 names when active. Status: `source_signed_nonexecutible_adaptation_only`.

Matched-control conclusion: The executable long-only sleeve is a momentum proxy; signed source identity remains non-executable overnight.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.
