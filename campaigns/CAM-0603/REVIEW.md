# CAM-0603 review — SSRN 3.4 Low-volatility anomaly

## Outcome

`retired_quote_execution_failure`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `qqq__vol126__hold6__reversal_long`, +397.61% fixed-base additive net.
- Selected executable adaptation: `qqq__lowvol63__hold1__q10` at +405.40%; development-only post-2024 return +12.83%; expanding walk-forward parameter-selection return +369.00%.
- The selected quote model was 09:40 marketable daily reset: -39.43% fixed-base net, 46.57% maximum drawdown, 3/12 positive months, and 99.85% position completeness. With 2 bps extra slippage per side it returned -49.42%.

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

Paper section 3.4, **Low volatility**. Source contract: Historical volatility over 126 to 252 trading days; buy bottom decile and short top decile; similar six- to twelve-month hold; no skip.

The structured survivor `qqq__lowvol_quality__top20__trend1` earned +85.2% net at 2 bps over its available development history and +26.2% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +26.0% with 7.0% drawdown, 8/4 positive/negative months, and 10.6% of positive P&L from the best five days.

Selection activity covered 87.5% of dates and averaged 18.37 names when active. Status: `low_risk_diversifier_unpromoted`.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.
