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

## 2026-08-10 deep-development checkpoint

Paper section 6.5, **Index volatility targeting**. Source contract: Risky weight w=target volatility/forecast volatility; risk-free weight=1-w; rebalance weekly/monthly or when relative weight change exceeds a threshold; optional leverage cap.

The structured survivor `QQQ__target15__vol20__monthly__thr20__def1` earned +116.2% net at 2 bps over its available development history and +18.3% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +18.0% with 9.4% drawdown, 8/4 positive/negative months, and 12.7% of positive P&L from the best five days.

Selection activity covered 98.9% of dates and averaged 1.72 names when active. Status: `modest_vol_target_unpromoted`.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.


## Split-repaired checkpoint (RUN-0020/RUN-0021/RUN-0023)

Prior strategy evidence is invalid because the inherited stock panel adjusted forward splits in the wrong direction. The repaired structured result is **provisional_execution_survivor** using `QQQ__target15__vol20__monthly__thr20__def1`. Its full repaired 2 bp additive return is 116.2% with 16.2% maximum drawdown; 09:40 SIP replay at +2 bp is 18.0% with 9.4% drawdown and 8/4 positive/negative months. This remains adapted development evidence; the May 2026 holdout was not accessed and promotion is blocked.
