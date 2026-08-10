# CAM-0600 review — SSRN 3.1 Price momentum

## Outcome

`promising_unpromoted_candidate`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `etf__12m_skip1__long`, +298.22% fixed-base additive net.
- Selected executable adaptation: `etf__mom252_skip0_hold3_q10__all__regime0` at +295.25%; development-only post-2024 return +128.90%; expanding walk-forward parameter-selection return +265.69%.
- The selected quote model was 09:30 marketable daily reset: +41.37% fixed-base net, 27.06% maximum drawdown, 9/12 positive months, and 100.00% position completeness. With 2 bps extra slippage per side it returned +31.41%.

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

Paper section 3.1, **Price momentum**. Source contract: Rcum=P(S)/P(S+T)-1; normally T=12 months, S=1 month; buy top decile and optionally short bottom decile; normally hold one month; equal or inverse-volatility weights.

The structured survivor `sp500__mom63_skip0__top3__liquid__panic1` earned +218.8% net at 2 bps over its available development history and +123.3% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +123.1% with 13.5% drawdown, 11/1 positive/negative months, and 13.5% of positive P&L from the best five days.

Selection activity covered 63.7% of dates and averaged 3.00 names when active. Status: `recent_momentum_component_promising_unpromoted`.

Matched-control conclusion: Panic defense sacrifices return but cuts historical drawdown materially; retain as a risk overlay.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.


## Split-repaired checkpoint (RUN-0020/RUN-0021/RUN-0023)

Prior strategy evidence is invalid because the inherited stock panel adjusted forward splits in the wrong direction. The repaired structured result is **provisional_execution_survivor** using `qqq__mom252_skip21__top3__liquid_trend__panic1`. Its full repaired 2 bp additive return is 235.4% with 19.6% maximum drawdown; 09:40 SIP replay at +2 bp is 73.0% with 13.5% drawdown and 9/3 positive/negative months. This remains adapted development evidence; the May 2026 holdout was not accessed and promotion is blocked.

RUN-0024 audited all 94 corrected QQQ/S&P split events. There were no duplicate
panel/symbol/date multipliers, no hard residual gap flags, and the largest
adjusted event gap was 4.45%.

## Individual high-frequency extension (RUN-0032 through RUN-0040)

Seven notable candidates were independently reconciled, adapted, tested across 231 parameter-neighborhood variants, quote replayed, and audited for listing history, contributor concentration, cadence, time stability, and redundancy. The best balanced development candidate is S&P MA200 momentum, top 10, with a 0.8 pairwise-correlation cap and a causal three-session smoothed momentum score. Exact 09:40 SIP replay plus 2 adverse bps per side returned +100.75% with 10.76% drawdown, 9/3 positive/negative months, 51.8% trade-session cadence, and 100% quote coverage. It remained +99.87% at quote plus 5 bps.

The adjacent five-day smoother returned +104.92% with 10.33% drawdown but missed the preferred cadence at 44.2%. Uncapped and dual-MA persistence variants returned about +112% but were 0.997 correlated and remain theme-heavy. Narrow top-five and long-term triple-MA variants were rejected despite higher headline return because their best five symbols accounted for 70-88% of positive symbol P&L; the triple-MA result became negative after removing them. The cluster residual remains a more independent daily watch candidate, while characteristic residual and true-daily alpha did not survive leave-top-five or high-cost scrutiny.

SNDK eligibility was causal: its membership-date close was its 200th available observation and positions were lagged to the next session. A stricter 252-observation control remained profitable, but concentration risk persists. No candidate is promoted, no broker margin was used, and the sealed holdout remains untouched.
