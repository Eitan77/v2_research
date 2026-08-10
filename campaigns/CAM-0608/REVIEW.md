# CAM-0608 review — SSRN 3.9.1 Multiple-cluster mean reversion

## Outcome

`retired_mechanism_exhausted`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `sp500__sector_proxy_clusters__return1`, -25.78% fixed-base additive net.
- Selected executable adaptation: `sp500__sector_clusters_f252_r5__long` at -6.23%; development-only post-2024 return -3.01%; expanding walk-forward parameter-selection return -62.56%.
- No executable long candidate cleared the 2 bps bar gate; quote replay was inapplicable.

## Judgment

The result is interpreted as development evidence only. The audit separated long-only implementable sleeves from overnight or no-stop short diagnostics, tested broad parameter neighborhoods, periods, costs, contributors, and causal universes, and did not select a full-sample winning ticker basket. `retired_mechanism_exhausted` is the strongest claim supported by the saved artifacts.

## Mandatory conclusion audit

- Source definition and implementation contract reconciled.
- Point-in-time universes, filing availability, sample attrition, and cutoff checks reconciled.
- Fixed-base additive accounting, no-margin gross cap, monthly/yearly path, drawdown, activity, costs, and concentration saved.
- Mechanism-consistent adaptations and development-only chronological checks completed.
- Every profitable execution-qualified best adaptation received SIP quote replay; direct-short signal-only variants were not called executable.
- Maximum loaded date is 2026-04-30 and holdout rows loaded are zero.
- Promotion remains false.

## 2026-08-10 deep-development checkpoint

Paper section 3.9.1, **Multiple-cluster mean reversion**. Source contract: Binary cluster-loadings regression without separate intercept; residuals are within-cluster demeaned returns and are cluster neutral; trade negative residuals.

The structured survivor `qqq__slow_residual_r10__top10__monthly` earned +144.9% net at 2 bps over its available development history and +29.1% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +27.1% with 32.7% drawdown, 8/4 positive/negative months, and 16.0% of positive P&L from the best five days.

Selection activity covered 92.3% of dates and averaged 10.00 names when active. Status: `fragile_residual_duplicate_unpromoted`.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.


## Split-repaired checkpoint (RUN-0020/RUN-0021/RUN-0023)

Prior strategy evidence is invalid because the inherited stock panel adjusted forward splits in the wrong direction. The repaired structured result is **provisional_execution_survivor** using `qqq__slow_residual_r10__top10__monthly`. Its full repaired 2 bp additive return is 210.7% with 28.3% maximum drawdown; 09:40 SIP replay at +2 bp is 44.7% with 21.0% drawdown and 8/4 positive/negative months. This remains adapted development evidence; the May 2026 holdout was not accessed and promotion is blocked.
