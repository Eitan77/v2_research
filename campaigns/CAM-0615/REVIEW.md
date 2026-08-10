# CAM-0615 review — SSRN 3.18 Statistical-arbitrage optimization

## Outcome

`retired_quote_execution_failure`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `qqq__momentum20__diag_cov60__unconstrained`, +11.79% fixed-base additive net.
- Selected executable adaptation: `qqq__fullcov_shrink50__mom60__long` at +65.58%; development-only post-2024 return +16.76%; expanding walk-forward parameter-selection return +25.84%.
- The selected quote model was 09:40 marketable daily reset: -26.35% fixed-base net, 30.92% maximum drawdown, 4/12 positive months, and 99.81% position completeness. With 2 bps extra slippage per side it returned -36.34%.

## Judgment

The result is interpreted as development evidence only. The audit separated long-only implementable sleeves from overnight or no-stop short diagnostics, tested broad parameter neighborhoods, periods, costs, contributors, and causal universes, and did not select a full-sample winning ticker basket. `retired_quote_execution_failure` is the strongest claim supported by the saved artifacts.

## Mandatory conclusion audit

- Source definition and implementation contract reconciled.
- Point-in-time universes, filing availability, sample attrition, and cutoff checks reconciled.
- Fixed-base additive accounting, no-margin gross cap, monthly/yearly path, drawdown, activity, costs, and concentration saved.
- Mechanism-consistent adaptations and development-only chronological checks completed.
- Every profitable execution-qualified best adaptation received SIP quote replay; direct-short signal-only variants were not called executable.
- Maximum loaded date is 2026-04-30 and holdout rows loaded are zero.
- Promotion remains false.

## 2026-08-10 deep-development checkpoint

Paper section 3.18, **Stat-arb optimization**. Source contract: Expected-return vector E and positive-definite covariance C; unconstrained weights proportional to inverse(C)E; normalize sum(abs(w))=1.

The structured survivor `qqq__fullcov_s50__mom60__positive_top10` earned +131.7% net at 2 bps over its available development history and +32.6% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +33.0% with 7.0% drawdown, 9/3 positive/negative months, and 12.5% of positive P&L from the best five days.

Selection activity covered 92.3% of dates and averaged 10.00 names when active. Status: `optimizer_not_isolated_unpromoted`.

Matched-control conclusion: Simple momentum dominates the adapted positive optimizer sleeve; source optimization is not isolated.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.


## Split-repaired checkpoint (RUN-0020/RUN-0021/RUN-0023)

Prior strategy evidence is invalid because the inherited stock panel adjusted forward splits in the wrong direction. The repaired structured result is **provisional_execution_survivor** using `qqq__fullcov_s50__mom60__positive_top5`. Its full repaired 2 bp additive return is 111.5% with 27.8% maximum drawdown; 09:40 SIP replay at +2 bp is 18.1% with 12.6% drawdown and 9/3 positive/negative months. This remains adapted development evidence; the May 2026 holdout was not accessed and promotion is blocked.
