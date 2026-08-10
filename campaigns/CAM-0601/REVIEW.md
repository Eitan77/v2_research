# CAM-0601 review — SSRN 3.2 Earnings momentum

## Outcome

`retired_quote_execution_failure`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `qqq__sue8__hold6__long`, +31.41% fixed-base additive net.
- Selected executable adaptation: `qqq__sue_sec__hold1__q20__long` at +91.42%; development-only post-2024 return +36.73%; expanding walk-forward parameter-selection return +43.67%.
- The selected quote model was 09:40 marketable daily reset: -13.97% fixed-base net, 30.76% maximum drawdown, 3/12 positive months, and 100.00% position completeness. With 2 bps extra slippage per side it returned -23.97%.

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

Paper section 3.2, **Earnings momentum**. Source contract: SUE=(latest announced quarterly EPS - EPS four quarters earlier)/standard deviation of this unexpected-earnings series over the last eight quarters; buy top decile and short bottom decile; typically hold six months.

The structured survivor `qqq__sue__hold3__top5__price20` earned +122.0% net at 2 bps over its available development history and +43.0% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +42.4% with 14.3% drawdown, 8/4 positive/negative months, and 11.9% of positive P&L from the best five days.

Selection activity covered 97.2% of dates and averaged 10.16 names when active. Status: `positive_but_inconsistent_unpromoted`.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.


## Split-repaired checkpoint (RUN-0020/RUN-0021/RUN-0023)

Prior strategy evidence is invalid because the inherited stock panel adjusted forward splits in the wrong direction. The repaired structured result is **provisional_execution_survivor** using `qqq__sue__hold6__top5__price20`. Its full repaired 2 bp additive return is 112.4% with 30.1% maximum drawdown; 09:40 SIP replay at +2 bp is 35.3% with 15.8% drawdown and 8/4 positive/negative months. This remains adapted development evidence; the May 2026 holdout was not accessed and promotion is blocked.
