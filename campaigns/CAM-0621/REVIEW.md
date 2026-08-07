# CAM-0621 review — SSRN 4.4 ETF IBS mean reversion

## Outcome

`retired_quote_execution_failure`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `etf_all__ibs__hold1__reversal_long_short`, +105.42% fixed-base additive net.
- Selected executable adaptation: `etf__ibs_le20__trend0__long` at +25.54%; development-only post-2024 return -19.68%; expanding walk-forward parameter-selection return -49.27%.
- The selected quote model was 09:40 marketable daily reset: -5.35% fixed-base net, 14.66% maximum drawdown, 6/12 positive months, and 99.78% position completeness. With 2 bps extra slippage per side it returned -14.96%.

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
