# CAM-0619 review — SSRN 4.1.1 Sector momentum with moving-average filter

## Outcome

`retired_quote_execution_failure`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `etf_sector__mom252__sma200__long`, +109.81% fixed-base additive net.
- Selected executable adaptation: `sector11__mom126__sma200__top1` at +112.80%; development-only post-2024 return +53.18%; expanding walk-forward parameter-selection return +51.75%.
- The selected quote model was 09:40 marketable daily reset: -12.06% fixed-base net, 45.25% maximum drawdown, 7/12 positive months, and 100.00% position completeness. With 2 bps extra slippage per side it returned -22.02%.

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

Paper section 4.1.1, **Sector momentum with MA filter**. Source contract: Buy winner only if its current price is above its daily 100- to 200-day MA; short loser only if below.

The structured survivor `sector11__mom63_skip0__monthly__top1` earned +130.0% net at 2 bps over its available development history and +36.9% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +39.5% with 10.0% drawdown, 8/4 positive/negative months, and 11.4% of positive P&L from the best five days.

Selection activity covered 87.7% of dates and averaged 1.00 names when active. Status: `winner_ma_gate_supported_unpromoted`.

Matched-control conclusion: The winner-MA gate improves both return and drawdown in the matched 63-day sector rule.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.
