# CAM-0610 review — SSRN 3.11 Single moving average

## Outcome

`execution_sensitive_unpromoted`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `etf__sma200__long`, +89.21% fixed-base additive net.
- Selected executable adaptation: `etf__sma200__long` at +79.87%; development-only post-2024 return +30.29%; expanding walk-forward parameter-selection return +52.50%.
- The selected quote model was 09:30 marketable daily reset: +0.77% fixed-base net, 18.81% maximum drawdown, 8/12 positive months, and 100.00% position completeness. With 2 bps extra slippage per side it returned -9.19%.

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
