# CAM-0617 review — SSRN 3.20 Alpha combos

## Outcome

`stopped_nonexecutable_short_signal`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `qqq__26alpha_combo__M20__E5`, +67.55% fixed-base additive net.
- Selected executable adaptation: `nan` at +nan%; development-only post-2024 return +33.58%; expanding walk-forward parameter-selection return +nan%.
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

Paper section 3.20, **Alpha combos**. Source contract: Standardize and demean alpha returns, truncate return-history loadings, normalize expected alpha returns, regress them on loadings, and set alpha weights to residual divided by alpha volatility; gross normalize.

The structured survivor `etf__alpha_M20_E5__top5__monthly__trend0` earned +196.0% net at 2 bps over its available development history and +58.9% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +64.3% with 15.7% drawdown, 9/3 positive/negative months, and 22.7% of positive P&L from the best five days.

Selection activity covered 95.6% of dates and averaged 4.39 names when active. Status: `leveraged_concentrated_tactical_unpromoted`.

Matched-control conclusion: Removing leveraged and inverse ETFs leaves positive but much weaker, cost-sensitive alpha-combo evidence.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.
