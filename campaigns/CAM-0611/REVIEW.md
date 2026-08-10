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

## 2026-08-10 deep-development checkpoint

Paper section 3.12, **Two moving averages**. Source contract: Long if short MA exceeds long MA and short on reverse; example 10/30; optional liquidation after one-day adverse move beyond 2 percent.

The structured survivor `qqq__ma50_200__weekly__top3__momentum` earned +308.0% net at 2 bps over its available development history and +120.2% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +121.9% with 12.4% drawdown, 9/3 positive/negative months, and 14.8% of positive P&L from the best five days.

Selection activity covered 88.3% of dates and averaged 3.00 names when active. Status: `two_ma_risk_filter_supported_unpromoted`.

Matched-control conclusion: The 50/200 gate improves return modestly and sharply reduces drawdown versus identical momentum ranking.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.


## Split-repaired checkpoint (RUN-0020/RUN-0021/RUN-0023)

Prior strategy evidence is invalid because the inherited stock panel adjusted forward splits in the wrong direction. The repaired structured result is **provisional_execution_survivor** using `sp500__ma50_200__weekly__top3__momentum`. Its full repaired 2 bp additive return is 250.0% with 26.4% maximum drawdown; 09:40 SIP replay at +2 bp is 161.5% with 11.9% drawdown and 11/1 positive/negative months. This remains adapted development evidence; the May 2026 holdout was not accessed and promotion is blocked.
