# CAM-0605 review — SSRN 3.7 Residual momentum

## Outcome

`retired_quote_execution_failure`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `qqq__ff3proxy_residual_risk_adjusted__long`, +63.86% fixed-base additive net.
- Selected executable adaptation: `qqq__residual_mom__q10__regime0` at +63.86%; development-only post-2024 return +39.14%; expanding walk-forward parameter-selection return +41.14%.
- The selected quote model was 09:40 marketable daily reset: -26.23% fixed-base net, 44.95% maximum drawdown, 4/12 positive months, and 99.93% position completeness. With 2 bps extra slippage per side it returned -36.23%.

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

Paper section 3.7, **Residual momentum**. Source contract: Regress 36 monthly excess returns on FF3 with intercept; compute 12-month residuals with one-month skip excluding the fitted intercept; rank residual mean/residual volatility; normally hold one month.

The structured survivor `qqq__resmom__top3__liquid` earned +115.9% net at 2 bps over its available development history and +30.3% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +28.4% with 22.5% drawdown, 9/3 positive/negative months, and 15.5% of positive P&L from the best five days.

Selection activity covered 54.6% of dates and averaged 3.00 names when active. Status: `fragile_high_drawdown_unpromoted`.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.


## Split-repaired checkpoint (RUN-0020/RUN-0021/RUN-0023)

Prior strategy evidence is invalid because the inherited stock panel adjusted forward splits in the wrong direction. The repaired structured result is **provisional_execution_survivor** using `sp500__resmom__top10__liquid_trend`. Its full repaired 2 bp additive return is 52.7% with 17.6% maximum drawdown; 09:40 SIP replay at +2 bp is 37.3% with 8.7% drawdown and 9/3 positive/negative months. This remains adapted development evidence; the May 2026 holdout was not accessed and promotion is blocked.
