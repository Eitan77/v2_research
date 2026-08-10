# CAM-0612 review — SSRN 3.13 Three moving averages

## Outcome

`execution_sensitive_unpromoted`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `qqq__sma3_10_21__long`, +46.30% fixed-base additive net.
- Selected executable adaptation: `etf__sma10_50_200__long` at +102.39%; development-only post-2024 return +24.31%; expanding walk-forward parameter-selection return +75.02%.
- The selected quote model was 09:40 marketable daily reset: +9.46% fixed-base net, 15.26% maximum drawdown, 9/12 positive months, and 100.00% position completeness. With 2 bps extra slippage per side it returned -0.50%.

## Judgment

The result is interpreted as development evidence only. The audit separated long-only implementable sleeves from overnight or no-stop short diagnostics, tested broad parameter neighborhoods, periods, costs, contributors, and causal universes, and did not select a full-sample winning ticker basket. `execution_sensitive_unpromoted` is the strongest claim supported by the saved artifacts.

## Mandatory conclusion audit

- Source definition and implementation contract reconciled.
- Point-in-time universes, filing availability, sample attrition, and cutoff checks reconciled.
- Fixed-base additive accounting, no-margin gross cap, monthly/yearly path, drawdown, activity, costs, and concentration saved.
- Mechanism-consistent adaptations and development-only chronological checks completed.
- Every profitable execution-qualified best adaptation received SIP quote replay; direct-short signal-only variants were not called executable.
- Maximum loaded date is 2026-04-30 and holdout rows loaded are zero.
- Promotion remains false.

## 2026-08-10 deep-development checkpoint

Paper section 3.13, **Three moving averages**. Source contract: Example 3/10/21; long when MA3>MA10>MA21, exit when MA3<=MA10; symmetric short.

The structured survivor `qqq__ma10_50_200__monthly__top3__momentum` earned +178.1% net at 2 bps over its available development history and +98.2% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +94.8% with 15.8% drawdown, 8/4 positive/negative months, and 14.5% of positive P&L from the best five days.

Selection activity covered 87.5% of dates and averaged 2.93 names when active. Status: `filtered_momentum_duplicate_unpromoted`.

Matched-control conclusion: The selected triple-MA rule materially trails ungated momentum and barely changes drawdown; it is a filtered momentum expression, not a distinct edge.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.
