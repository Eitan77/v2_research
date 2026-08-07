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
