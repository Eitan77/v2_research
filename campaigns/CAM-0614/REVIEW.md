# CAM-0614 review — SSRN 3.15 Donchian channel

## Outcome

`retired_quote_execution_failure`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `qqq__donchian_close20__long`, +72.38% fixed-base additive net.
- Selected executable adaptation: `qqq__donchian10__long` at +82.26%; development-only post-2024 return +15.74%; expanding walk-forward parameter-selection return +67.20%.
- The selected quote model was 09:40 marketable daily reset: -27.79% fixed-base net, 36.26% maximum drawdown, 4/12 positive months, and 99.98% position completeness. With 2 bps extra slippage per side it returned -37.79%.

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
