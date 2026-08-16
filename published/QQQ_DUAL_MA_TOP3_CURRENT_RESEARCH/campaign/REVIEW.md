# CAM-0611 review — SSRN 3.12 Two moving averages

## Outcome

`execution_sensitive_unpromoted`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `etf__sma10_30__long`, +80.81% fixed-base additive net.
- Selected executable adaptation: `etf__sma50_200__long` at +110.43%; development-only post-2024 return +37.78%; expanding walk-forward parameter-selection return +16.00%.
- The selected quote model was 09:40 marketable daily reset: +7.15% fixed-base net, 13.86% maximum drawdown, 8/12 positive months, and 100.00% position completeness. With 2 bps extra slippage per side it returned -2.81%.

## Judgment

The result is interpreted as development evidence only. The audit separated long-only implementable sleeves from overnight or no-stop short diagnostics, tested broad parameter neighborhoods, periods, costs, contributors, and causal universes, and did not select a full-sample winning ticker basket. `execution_sensitive_unpromoted` is the strongest claim supported by the saved artifacts.

## Partial profit-taking checkpoint

RUN-0054 and RUN-0055 tested 23 causal partial-trim configurations strictly through April 30, 2026. Weekly-reset trims produced a smooth but small tradeoff: selling 75% after a 15% within-cycle gain reduced exact 2-bp drawdown from 17.52% to 16.59%, but also reduced return from 362.83% to 361.88% and recent-12 return from 117.09% to 116.51%. Persistent trims substantially damaged return.

The upper-bound 20%/75% trim reached 365.72% with 16.97% drawdown, but its incremental P&L was concentrated in 2020 (+11.96 points) and was negative in 2023, 2024, and 2025; it had no triggers in the latest in-sample year. Fixed partial profit-taking is therefore not adopted. It does not robustly protect the strategy's recent reversal exposure.

RUN-0056 then tested progressive ladders, including the requested sale of 20% of original weight at each 5-point gain. Exact 2-bp return fell to 352.67% from 362.83%, recent-12 return fell to 109.56% from 117.09%, and drawdown improved only from 17.52% to 16.98%; worst month and positive-month count were unchanged. Selling 20% every 10 points was less harmful but still dominated after exact fills. Repeated fixed trimming therefore does not improve this continuation strategy.

RUN-0057 tested a much finer ladder: sell 5% of original weight after each additional one-point completed-close gain, through +20%. Exact 2-bp return was 346.99%, recent-12 was 104.49%, drawdown was 16.17%, positive/negative months improved from 48/25 to 50/23, worst month improved from -16.62% to -15.50%, and worst year improved from -7.49% to -3.33%. This is a real smoothing tradeoff, but not a dominant strategy: it gives up 15.84 full-history and 12.61 recent-12 return points and expands quote roles from 383 to 1,958. It is not adopted as the locked default.

## Self-financing deployment checkpoint

RUN-0058 rebuilt the locked signal as an actual fractional-share cash account. Every scheduled Monday used exact 09:30/09:40 SIP marks, sold and trimmed before buying, solved equal liquidation-value targets from current equity, retained a 0.5% cash reserve, and prohibited negative cash or gross exposure above equity. All 12 planned cost/reserve/cadence variants passed; 1,137/1,138 roles per clock were directly quoted and the final XLNX regular-session bid resolved the acquisition terminal event.

At exact quotes plus 2 bp per side, weekly equalization grew normalized equity from 1.0 to 16.6118 (+1,561.18% compounded) and returned +195.12% in the latest discovery year. This is not comparable to the primary fixed-base additive +362.83% headline: the compounded deployment path had a 42.33% maximum drawdown, a -18.07% worst year, and a 157-session recovery. Equalizing only when membership changed ended at 17.8639 with 41.99% drawdown and 709 orders versus 1,138 for weekly equalization. Weekly equalization is executable but not the preferred development frontier, and neither architecture is live-ready without frozen holdout and paper reconciliation.

## Authorized self-financing holdout checkpoint

RUN-0059 extended the frozen change-only architecture through August 14, 2026 without changing the signal, ranking, target count, execution clock, reserve, or costs. Exact SIP quote coverage was 100%, cash remained nonnegative, and gross exposure never exceeded equity. Compounded monthly returns were +45.99% in May, +21.23% in June, -32.76% in July, and +22.18% for August through the 14th. The combined holdout return was +45.40%.

The path is not a consistency success. Equity peaked on June 22, fell 44.61% by July 29, and remained 23.32% below that peak on August 14 despite the rebound. This is deployment-account evidence, not the primary fixed-base research convention. It confirms both unusually strong momentum capture and severe concentrated reversal risk; promotion remains false and the holdout must not be used for retuning.

## Replacement-only capital handling and entry extension

RUN-0065 tested a literal self-financing replacement architecture through the sealed discovery cutoff only. At each target change it sold outgoing names, split available proceeds among entrants, and left continuing positions untouched. Exact SIP execution plus two adverse basis points ended at 21.4518x, versus 17.8639x for membership-change equalization and 16.6118x for weekly equalization. Maximum drawdown improved slightly to 41.11%, and orders fell to 383 from 709 and 1,138. The result survives ten basis points at 19.5663x. This is a materially better development deployment frontier, not a new signal.

The improvement comes with genuine drift risk: TSLA reached 63.29% of account equity on January 8, 2021, and the largest position exceeded 50% for 501 sessions. Replacement-only is therefore preserved as the leading capital-handling challenger but is not promoted. A coarse, prospectively frozen concentration boundary is the unresolved test; unrestricted drift is not automatically a live recommendation.

RUN-0066 separately tested new-entry-only 21-session extension vetoes at 10%, 20%, 30%, 40%, and 50%. Existing holdings were exempt, and blocked entrants were replaced by the next 126/21-ranked eligible name. Exact quote results were nonmonotonic. The 10-40% branches reduced full or recent return; the 50% branch improved fixed-base return only from 362.83% to 368.14%, reduced drawdown from 17.52% to 17.32%, changed only nine candidates, and left recent return unchanged. Reject the veto as a thin post-hoc cleanup and do not combine it with replacement-only handling.

## Fixed initial risk-budget checkpoint

RUN-0060 tested a cash-valid bridge between fixed-base research and live deployment. Every scheduled Monday it targeted at most one original capital unit across the selected three names, swept equity above that risk budget to cash, used available cash to restore losing slots, and scaled below one only if equity could no longer support the original budget. Exact SIP spreads plus two basis points per side were applied; cash never became negative and rebalance target gross never exceeded one.

Through the discovery cutoff, equity ended at 4.5310 (+353.10%) with an 18.41% maximum drawdown. Extending the already-observed May-August interval produced +10.92% with 10.40% drawdown: +9.33% May, +4.86% June, -7.46% July, and +4.56% through August 14. The fully compounded change-only account made +45.40% over the same interval but drew down 44.61%. Fixed risk is therefore a genuine return-for-risk tradeoff and a coherent small-account architecture, not a free alpha improvement. Because it was proposed after observing the holdout, May-August is diagnostic rather than fresh OOS evidence.

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
## Individual high-frequency follow-up

Persistence-three raises the exact quote result to +112.19% with 10.59% drawdown and 61.0% trade-session cadence. Its daily P&L correlation with the analogous single-MA candidate is 0.997, so it is a redundant expression rather than a separate discovery and should not be combined for cosmetic diversification.

## Exit-overlay checkpoint (RUN-0033)

Seventeen causal next-open exit overlays were tested without changing the locked weekly entry. Exact SIP replay at two additional basis points found no profit improvement over the +362.83% control. A 5% close-triggered stop reduced drawdown from 17.52% to 16.37% and improved the worst month from -16.62% to -13.75%, but reduced full return to +353.44% and recent return to +111.58%. Trimming half after a 5% gain improved the monthly count from 48/25 to 50/23 and drawdown to 16.97%, but reduced recent return to +108.16%. Fixed take-profits and tight trailing exits clipped the momentum winners. The unchanged weekly exit remains the profit-maximizing default; stop5 is only a defensive alternative.

## Staircase-ratchet checkpoint (RUN-0034)

An activated staircase ratchet succeeded where continuous trailing stops failed. The leading rule activates after a 15% position gain, locks breakeven, raises the floor by 5 percentage points for each additional 5-point completed-close gain, and exits next open only after two consecutive closes at or below the floor. Exact SIP replay plus two basis points returned +381.03%, versus +362.83% control, while reducing drawdown from 17.52% to 16.36% and raising recent return from +117.09% to +118.61%. Adjacent 15/5 one-close and 10/5 two-close rules also improved full return and drawdown, and the leader remained +369.59% at ten basis points. This is the leading adapted exit challenger, but remains development-selected rather than independently confirmed.

## Winner/loser and selection checkpoint (RUN-0035/RUN-0036)

The 193 exact-quote baseline episodes average +5.64% on allocated position capital but have a median of only +0.86%; win rate is 57.5%, with 25 gains of at least 20% and 21 losses of at least 10%. The best entry-time price features are not defensive: extreme momentum, volatility, liquidity, and distance above SMA200 produce both the largest winners and large losers. This explains why tight stops and fixed take-profits fail and why a delayed ratchet is directionally sensible. Revenue growth, EPS growth, relative volume, tighter liquidity, and blended ranks are nonmonotonic or fail direct tests. Market-cap results are invalid because historical share-count/split scaling creates impossible values and must not support a screen.

A +25% absolute momentum floor combined with the ratchet reaches +405.53% and 15.74% drawdown after exact quotes, but its entire +24.50-point incremental benefit occurs in 2022; it changes net P&L by zero in every other calendar year and does nothing recently. It is therefore a regime-specific historical cleanup, not a promoted improvement. The locked weekly control remains the default; the staircase ratchet remains only a forward-tracking challenger.

## Weekday checkpoint (RUN-0038)

All five weekly signal weekdays were compared with identical rules. Monday and Thursday survived bar stage and received exact SIP replay. At two additional basis points, Friday returned +362.83% with 17.52% drawdown and +117.09% recent12; Monday returned +367.83% with 18.11% drawdown and +124.69% recent12, with the same 48/25 monthly count; Thursday returned +377.57% but drawdown rose to 22.25% and monthly consistency worsened to 46/27. No weekday dominates. Friday remains locked for simplicity and lower adaptation; Monday is only a modest recent forward-tracking challenger.

## Friday near-close execution checkpoint (RUN-0039)

A causal same-session implementation used the first SIP quote after 15:50 ET as the signal midpoint and the first marketable quote after 15:55 for execution, adjusted to close-minus-ten and close-minus-five minutes on shortened sessions. Signal coverage was 99.756% across 36,493 point-in-time QQQ roles; all 395 execution roles were filled. At two additional basis points it returned +371.07%, versus +362.83% for Friday-close/Monday execution, and recent12 improved from +117.09% to +122.57%. However drawdown worsened from 17.52% to 18.38%, monthly consistency from 48/25 to 45/28, and recent positive months from 9 to 8. It is a viable aggressive timing variant but not a dominant replacement.

## First authorized out-of-sample month (RUN-0040)

The unchanged locked QQQ dual-MA top-three baseline earned +45.37% net fixed-base P&L in May 2026 after exact 09:40 SIP execution and two additional basis points per turnover. Peak-relative drawdown was 10.33%; 12 of 20 sessions and four of five weekly buckets were positive. The result is genuine first-month confirmation under the frozen rules, but it is not broad: gross contribution came from SNDK +17.67 points, MU +13.77, WDC +9.53, and STX +3.53. One unusually strong month in one related industry cluster is not sufficient for promotion or retuning. No data after May 31 was accessed, and the later holdout remains sealed.

## June-July authorized out-of-sample extension (RUN-0041)

The baseline remained unchanged between months. June earned +15.30% net, but July lost -26.85%, leaving June-July at -11.55%. Only three of nine weekly buckets were positive. On the continuous May-July path, cumulative fixed-base P&L remained +33.82%, but the peak-relative drawdown reached 26.98% from June 18 through July 29 and was unresolved at July 31. July's gross loss was concentrated in SNDK (-14.79 points), MU (-7.32), and WDC (-3.58), confirming that the same cluster responsible for May's exceptional gain also creates the tail risk. Exact quote coverage was 100%. Point-in-time QQQ membership ends June 24; the last causally known membership was carried forward for 26 later sessions and is an explicit evidence limitation. The result fails the desired consistency profile and is not promoted or retuned.

## August 1-10 authorized extension (RUN-0042)

Fresh Alpaca data through the completed August 10 session gives +10.09% net fixed-base P&L for August 3-10, versus +4.73% for QQQ. Four of six sessions were positive. The bar-stage strategy gain was +8.91%; exact 09:40 rotation execution contributed a favorable 1.19 points net of the two-basis-point convention, primarily because ARM was bought well below its 09:30 midpoint. May-through-August 10 cumulative net P&L is +43.92%, while the continuous maximum drawdown remains 26.98%. This is a strong rebound, not resolution of the concentrated tail-risk problem.

## Five-session overextension substitution checkpoint (RUN-0070--0072)

The current SNDK episode motivated a development-only, explicitly post-hoc test of fixed five-session return ceilings. At 25, 30, and 35 percent, substituting the next-ranked eligible name raised fixed-base return only modestly from +362.83% to +364.52%, +367.34%, and +368.25%; the cells changed only eight, five, and one selections. Lower 10--20 percent ceilings damaged return and raised turnover.

Full-history exact 09:40 SIP replay at two additional basis points reproduced the self-financing control and increased compounded return to +1762.31%, +1780.86%, and +1809.57% for the three sparse thresholds. None changed the 41.99% maximum drawdown or -15.07% worst month, while recent12 compounded return fell from +192.51% to +188.24%. The ending-equity lift is path-dependent rather than a demonstrated tail-risk repair. Preserve the baseline and do not use the observed SNDK episode to promote this filter.

## Weekly dependence checkpoint (RUN-0073)

Across 316 completed discovery weeks, exact compounded weekly returns had 0.002 lag-one autocorrelation. A loss followed a prior loss 43.1% of the time versus 43.8% after any non-loss (Fisher exact p=0.91), so losses did not cluster. Two-loss streaks instead preceded a +2.15% average next week and 61.0% win rate. Gains of at least 10% preceded a +1.92% average next week and 66.7% win rate, although the worst such next week was -15.29%; strong weeks therefore provide no reliable reversal warning.

The only material state variable was membership churn. Weeks after a rebalance averaged +0.24% with 49.4% wins, versus +2.03% and 65.0% after no rebalance. The relationship was directionally stable across broad chronological partitions, but the weaker state still had positive expected return. A frictionless weekly cash-gate diagnostic reduced additive return from +330.94% to +290.38% before extra exit and re-entry costs. Treat rebalance state as a risk-monitoring context, not a validated blocker or sizing rule.

## Monday market-state checkpoint (RUN-0074/RUN-0075)

A red QQQ open was historically favorable, not a warning. QQQ gaps of at least -0.5% preceded +2.23% average strategy cycles with 65.7% wins across 67 observations. A negative first ten minutes reduced the mean to +0.74% and win rate to 51.1%, but remained positive in aggregate and was chronologically unstable as a blocker.

The only blocker-shaped observation was post-hoc gap-up exhaustion. QQQ gaps of at least +1% had negative ensuing fixed-base P&L in both 2020-2022 and 2023-2026. Exact 09:40 SIP replay plus two basis points raised fixed-base return from +362.83% to +409.89% and reduced drawdown from 17.52% to 15.19% when those 31 cycles were held in cash. A related SPY +1% rule reached +408.05%. However the QQQ threshold neighborhood was irregular, recent12 performance declined, worst-month protection did not improve, and compounded worst month worsened. This is a forward-paper research candidate, not a justified live override. Preserve the baseline and do not delay entry merely because Monday opens down.
