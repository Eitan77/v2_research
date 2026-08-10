# CAM-0624 review — SSRN 15.3.1 Distress risk puzzle risk management

## Outcome

`retired_quote_execution_failure`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `sp500__chs_proxy__reversal_long__target15`, +56.92% fixed-base additive net.
- Selected executable adaptation: `sp500__chs_safe_q20__target15__vol126` at +56.54%; development-only post-2024 return +38.41%; expanding walk-forward parameter-selection return +35.66%.
- The selected quote model was 09:40 marketable daily reset: -27.78% fixed-base net, 34.37% maximum drawdown, 2/12 positive months, and 99.94% position completeness. With 2 bps extra slippage per side it returned -35.66%.

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

Paper section 15.3.1, **Distress risk management**. Source contract: Scale HMD by target volatility divided by prior-year realized daily HMD volatility; target normally 10 to 15 percent.

The structured survivor `qqq__chs_safe__top5__liquid__target8` earned +50.5% net at 2 bps over its available development history and +17.8% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +17.5% with 3.7% drawdown, 9/3 positive/negative months, and 12.2% of positive P&L from the best five days.

Selection activity covered 85.0% of dates and averaged 5.00 names when active. Status: `low_drawdown_distress_component_unpromoted`.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.


## Split-repaired checkpoint (RUN-0020/RUN-0021/RUN-0023)

Prior strategy evidence is invalid because the inherited stock panel adjusted forward splits in the wrong direction. The repaired structured result is **provisional_execution_survivor** using `qqq__chs_safe__top5__liquid__target15`. Its full repaired 2 bp additive return is 114.0% with 15.1% maximum drawdown; 09:40 SIP replay at +2 bp is 32.8% with 6.6% drawdown and 9/3 positive/negative months. This remains adapted development evidence; the May 2026 holdout was not accessed and promotion is blocked.
