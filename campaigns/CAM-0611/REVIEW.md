# CAM-0611 review — SSRN 3.12 Two moving averages

## Outcome

`execution_sensitive_unpromoted`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `etf__sma10_30__long`, +80.81% fixed-base additive net.
- Selected executable adaptation: `etf__sma50_200__long` at +110.43%; development-only post-2024 return +37.78%; expanding walk-forward parameter-selection return +16.00%.
- The selected quote model was 09:40 marketable daily reset: +7.15% fixed-base net, 13.86% maximum drawdown, 8/12 positive months, and 100.00% position completeness. With 2 bps extra slippage per side it returned -2.81%.

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
