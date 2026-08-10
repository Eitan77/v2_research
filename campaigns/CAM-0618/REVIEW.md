# CAM-0618 review — SSRN 4.1 Sector momentum rotation

## Outcome

`retired_quote_execution_failure`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `etf_sector__formation252__hold3__long`, +176.91% fixed-base additive net.
- Selected executable adaptation: `sector11__mom252__hold6__top1` at +176.41%; development-only post-2024 return +48.26%; expanding walk-forward parameter-selection return +112.99%.
- The selected quote model was 09:40 marketable daily reset: -1.49% fixed-base net, 25.37% maximum drawdown, 7/12 positive months, and 100.00% position completeness. With 2 bps extra slippage per side it returned -11.45%.

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

Paper section 4.1, **Sector momentum rotation**. Source contract: Rank sector ETFs by six- to twelve-month cumulative return; buy top decile and hold one to three months; optional bottom-decile short.

The structured survivor `sector11__mom252_skip21__weekly__top3` earned +111.6% net at 2 bps over its available development history and +25.2% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +26.4% with 5.6% drawdown, 11/1 positive/negative months, and 10.6% of positive P&L from the best five days.

Selection activity covered 84.7% of dates and averaged 2.98 names when active. Status: `smooth_sector_diversifier_unpromoted`.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.
