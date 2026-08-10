# CAM-0620 review — SSRN 4.1.2 Dual-momentum sector rotation

## Outcome

`retired_quote_execution_failure`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `etf_sector__mom252__spy_sma200__fallback_GLD`, +107.50% fixed-base additive net.
- Selected executable adaptation: `sector11__mom126__sma200__fallback_GLD` at +111.66%; development-only post-2024 return +54.82%; expanding walk-forward parameter-selection return +65.37%.
- The selected quote model was 09:40 marketable daily reset: -13.89% fixed-base net, 45.58% maximum drawdown, 7/12 positive months, and 100.00% position completeness. With 2 bps extra slippage per side it returned -23.85%.

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

Paper section 4.1.2, **Dual-momentum sector rotation**. Source contract: Buy relative-momentum sector winners only if broad index price is above its 100- to 200-day MA; otherwise hold an uncorrelated gold or Treasury ETF.

The structured survivor `sector11__mom63_skip0__monthly__top1` earned +117.9% net at 2 bps over its available development history and +34.5% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +35.9% with 9.3% drawdown, 8/4 positive/negative months, and 13.8% of positive P&L from the best five days.

Selection activity covered 98.9% of dates and averaged 1.00 names when active. Status: `dual_market_gate_supported_unpromoted`.

Matched-control conclusion: The broad-market gate roughly halves drawdown and modestly improves return, with BIL defense and no margin.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.


## Split-repaired checkpoint (RUN-0020/RUN-0021/RUN-0023)

Prior strategy evidence is invalid because the inherited stock panel adjusted forward splits in the wrong direction. The repaired structured result is **provisional_execution_survivor** using `sector11__mom63_skip0__monthly__top1`. Its full repaired 2 bp additive return is 117.9% with 16.9% maximum drawdown; 09:40 SIP replay at +2 bp is 35.9% with 9.3% drawdown and 8/4 positive/negative months. This remains adapted development evidence; the May 2026 holdout was not accessed and promotion is blocked.
