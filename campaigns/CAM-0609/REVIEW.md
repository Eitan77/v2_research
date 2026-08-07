# CAM-0609 review — SSRN 3.10 Weighted-regression mean reversion

## Outcome

`retired_mechanism_exhausted`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `sp500__weighted_regression__return1`, -39.81% fixed-base additive net.
- Selected executable adaptation: `sp500__weighted_residual_r2__long` at -20.68%; development-only post-2024 return -9.11%; expanding walk-forward parameter-selection return -54.47%.
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
