# CAM-0622 review — SSRN 6.5 Index volatility targeting with risk-free asset

## Outcome

`promising_unpromoted_candidate`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `etf__QQQ__target15__vol252__weekly`, +71.75% fixed-base additive net.
- Selected executable adaptation: `QQQ__target15__vol63__monthly__BIL` at +101.31%; development-only post-2024 return +43.35%; expanding walk-forward parameter-selection return +85.70%.
- The selected quote model was 09:30 marketable daily reset: +20.46% fixed-base net, 9.98% maximum drawdown, 8/12 positive months, and 100.00% position completeness. With 2 bps extra slippage per side it returned +10.50%.

## Judgment

The result is interpreted as development evidence only. The audit separated long-only implementable sleeves from overnight or no-stop short diagnostics, tested broad parameter neighborhoods, periods, costs, contributors, and causal universes, and did not select a full-sample winning ticker basket. `promising_unpromoted_candidate` is the strongest claim supported by the saved artifacts.

## Mandatory conclusion audit

- Source definition and implementation contract reconciled.
- Point-in-time universes, filing availability, sample attrition, and cutoff checks reconciled.
- Fixed-base additive accounting, no-margin gross cap, monthly/yearly path, drawdown, activity, costs, and concentration saved.
- Mechanism-consistent adaptations and development-only chronological checks completed.
- Every profitable execution-qualified best adaptation received SIP quote replay; direct-short signal-only variants were not called executable.
- Maximum loaded date is 2026-04-30 and holdout rows loaded are zero.
- Promotion remains false.
