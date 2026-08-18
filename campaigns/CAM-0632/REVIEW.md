# CAM-0632 checkpoint review after RUN-0002

The initial high-frequency scalping thesis failed under realistic turnover. The run executed all 576 frozen signal variants and 2,880 costed rows across the two catalog-complete core pairs from June 21, 2019 through April 30, 2026. Decisions used completed start-stamped bars, entries used the next bar open, positions did not overlap within a pair/variant, and every query enforced the discovery cutoff.

The dense result explains why repeating a nominal 0.1% target is not automatically a printer. The highest zero-cost recent result, one-minute SMH continuation held ten minutes, made only 0.37 bp gross per trade across 42,763 trades, about 25 per session. A one-basis-point cost on each side exceeds that expectancy. Leveraged-lag convergence also became negative at one basis point per side. More frequency amplified friction rather than edge.

Only one of the 576 variants remained positive in all three broad chronological blocks at five basis points per side: after an absolute one-minute QQQ move of at least 40 bp, buy the opposite TQQQ/SQQQ leg at the next minute open and hold fifteen minutes. It produced +49.65% fixed-base additive net return over 329 trades, 15.96% maximum drawdown, and block returns of +5.81%, +23.86%, and +19.98%. The top ten absolute trades were only 15.55% of absolute movement, so it is not a single-print artifact.

That row is not consistent or currently strong. It averaged only 0.19 trades per session, had 24.10% positive months when zero months are included, and returned -3.36% over the latest twelve months at five basis points per side. At ten basis points per side the first chronology block became negative and recovery stretched to 465 sessions. No quote replay is justified yet.

Decision: dense scalp families are rejected under the tested bar economics, but the large-impulse overshoot-reversal mechanism remains a principled diagnostic. The next run will test broad threshold/hold neighbors, one completed reversal-confirmation bar, direction and time-of-day attribution, and whether actual leveraged-ETF overshoot improves selection. A recent-only maximum will not rescue a weak full path.

## RUN-0003 overshoot neighborhood

The reversal clue broadened into six all-block-positive rows at five basis points per side, four with positive latest-twelve-month totals. This supports a real but sparse large-impulse reversal family rather than one isolated cell. It still does not meet the requested consistency profile.

The recent-focused SMH row used a 60 bp shock and fifteen-minute hold, returned +41.56% additive over 147 trades, and made +10.75% in the latest twelve months, but had 17.16% drawdown, 247-session recovery, and only 22.89% positive months. The strongest full/risk row required a 50 bp shock, twenty-minute hold, and 20 bp directional leveraged overshoot; it returned +84.32% with 7.96% drawdown but only 108 trades, 18.07% positive months, and 616-session recovery. A one-bar opposite-sign confirmation improved the QQQ representative to +56.26% with 8.41% drawdown and all blocks positive, but latest-twelve-month return was only +0.50%.

The mechanism is therefore neither high-frequency nor a monthly printer. It has nevertheless earned exact marketable SIP replay because three distinct representatives survive broad threshold, hold, confirmation, and cost neighborhoods. Quote replay will determine whether next-bar opens hid spread/latency damage; it will not be used to rescue a losing bar family or justify more threshold mining.

## RUN-0005 dense time-of-day split

The last obvious aggregate-bar omission did not repair dense scalping. A frozen 300-variant grid tested one-minute continuation and reversal across two ETF trios, three shock sizes, five holds, and five broad session buckets. Selection used only June 2019 through December 2023 and required at least 250 trades, positive net return in both development halves at two basis points per side, positive mean trade, and at least half of active days green.

Only afternoon SMH/SOXL/SOXS continuation after a 20 bp one-minute SMH move survived development. It earned +25.43% additive over 936 trades at two basis points per side, with +19.66% and +5.76% in the two development halves. Evaluated unchanged from January 2024 through April 2026, it lost 7.78% even at one basis point per side and 14.52% at two; both validation halves and the latest twelve months were negative. The active-day win rate fell from 51.3% to 34.6% at the selection cost.

Decision: reject time-of-day conditionality as a dense-scalp repair. Its only selected rule was a development-era effect that reversed rather than weakened marginally. Further session slicing would be historical chart fitting.

## RUN-0004 marketable SIP replay

All 461 frozen trades received both an entry and exit SIP NBBO. The initial three-second forward lookup missed only two SOXS exits on March 18, 2020; before any economics were calculated, the horizon was widened to thirty seconds while retaining the first quote at or after the frozen target plus latency. No prior quote was substituted and no trade was dropped. Additional fail-fast repairs added a unique trade key and numeric quote types. The final run executed all 36 latency/slippage rows with 100% role coverage.

At 250 ms plus two adverse basis points on both sides beyond the observed ask-entry/bid-exit spread, the confirmed QQQ reversal earned +61.96% additive over 206 trades, with all chronology blocks positive, 7.92% drawdown, 61.1% green active days, and +0.75% in the latest twelve months. The selective SMH overshoot rule earned +65.27% over 108 trades, with all blocks positive and 9.31% drawdown, but only +1.17% in the latest twelve months and a 615-session recovery. The recent-focused SMH rule earned +19.58%, but its first block was negative, drawdown was 20.55%, and it had not recovered by the cutoff.

These are execution survivors, not a printer. Their positive-month fractions remain only 18.1% to 26.5% because signals occur roughly two to four times per month. Several 2020 and April 2025 trades are extreme, and marketable quote returns differ enough from bar returns to require explicit price-scale and timestamp reconciliation. A dedicated adversarial audit is the next gate; no candidate is frozen or promoted from this replay alone.

## RUN-0006 adversarial audit

The audit reconciled all 461 quote trades to their frozen RUN-0003 identities and raw one-minute entry/exit bars. There were zero trade-key mismatches, half/double price-scale failures, crossed or nonpositive NBBOs, missing displayed-size units, or latency-window failures. Median quote-versus-bar return differences were -3.07 bp for QQQ and -11.50 bp for the selective SMH rule; the larger SMH extremes occurred during violent minutes but did not reflect a split-scale mismatch.

Two rules passed every prespecified economic attack. At 1,000 ms plus five adverse basis points per side, QQQ retained +50.91%, +0.55% recent-twelve-month return, and block returns of +25.26%, +19.04%, and +6.61%. Removing its best date left +47.66%; excluding both the COVID shock window and April 2025 left +26.56%; its ten largest absolute trades were 18.6% of absolute movement. The selective SMH rule retained +57.53%, +0.37% recent-twelve-month return, and +3.00%, +13.09%, and +41.45% blocks. Removing its best date left +37.30%, jointly excluding both shock windows left +17.56%, and its top-ten share was 32.9%.

The recent-focused SMH rule failed because its first chronology block remained negative under stress. It is rejected. The other two are frozen as adapted, quote-validated research candidates. They were shaped using discovery history, so this is not genuine out-of-sample evidence; their signal rate and positive-month frequency remain far below the requested daily consistency. The next useful test is a cash-only fixed-sleeve combination and then forward paper observation, not more threshold search.

## RUN-0007 fixed-sleeve portfolio and checkpoint decision

The permanent 50/50 sleeves respected the cash-only limit: observed maximum concurrent gross exposure was exactly 1.0 across 68 overlapping entries. The two daily paths had only 0.34 active-day correlation. Under 250 ms plus two adverse basis points per side, the portfolio earned +63.62% additive over 314 trades, with +18.50%, +18.43%, and +26.68% chronology blocks, 5.45% drawdown, 42-session recovery, and 16.3% top-ten absolute-trade concentration. Both sleeves contributed almost equally.

At the prespecified 1,000 ms plus five-basis-point-per-side stress, it retained +54.22%, +14.13%, +16.06%, and +24.03% blocks, 5.89% drawdown, 75-session recovery, and +0.46% in the latest twelve months. This is credible historical profitability for an adapted event-reversal portfolio.

It is not the originally hoped-for high-frequency printer. The portfolio averaged 0.18 trades per session, was active on only 6.83% of sessions, and had positive return in 26.51% of calendar months when zero months are included. The dense families and development-selected time bucket failed. The result that worked is a sparse structural shock-reversal strategy whose recent expected profit is modest.

Decision: freeze the exact cash-only rules in `FORWARD_PAPER_SPEC.md` and recommend forward paper observation. Do not inspect the sealed post-April-2026 history to manufacture confirmation. Review only after the later of twelve forward months or fifty completed trades, with actual broker fills, unchanged thresholds and holds, and both sleeves contributing positively. No live-capital or guaranteed-profit claim is made.

## RUN-0008 displayed-depth capacity

The percentage-return path is not automatically scalable. SIP sizes are round-lot units, so the audit converted each unit to 100 shares and measured the smaller of entry-ask and later exit-bid displayed notional. At the reference engine's conservative 5% participation, median supported full-portfolio notional was only $887 and the minimum was $184. Only 27.4% of trades supported a $2,000 portfolio's 50% sleeve on both sides; for $10,000 the fraction was 9.2%. At 25% participation those figures improved to 82.2% and 27.4%. Consuming the full displayed top of book would support $2,000 on every observation, but that is a diagnostic upper bound rather than a prudent assumption.

Exit depth is observed after entry and cannot causally select trades. Accordingly, these fractions do not define an alternate profitable subset and no PnL row was dropped. The next run must cap size using entry-time depth only and preserve every subsequent outcome. Until then, the fixed-base portfolio establishes small-unit edge, not scalable account returns.

## RUN-0009 causal entry-depth sizing

For a $2,000 account with 50% target sleeves and the prespecified 5% entry-depth cap, the 1,000 ms/five-basis-point stress retained +42.31% additive return. All blocks remained positive at +8.88%, +4.76%, and +28.67%; drawdown was 5.89%. Average sleeve utilization was 64.9%, only 43.6% of orders reached their whole-share target, and the latest twelve months earned just +0.20%.

Scaling is poor. At $10,000 and the same 5% cap, average utilization fell to 32.3%, median utilization to 13.9%, and recent-twelve-month return became -0.22%, although the full path and all blocks remained positive. Account-size rows are diagnostics, not a menu from which to select the best historical weighting.

Most importantly, 26.8% of the $2,000 orders were larger than 5% of the displayed bid when they later exited. Those trades were not removed or resized using future information, but the existing five-basis-point exit stress may understate the cost of sweeping them. A punitive unsupported-exit penalty is still required before the forward-paper handoff is final.

## RUN-0010 punitive unsupported-exit impact

All 84 depth-unsupported exits remained in the ledger. Adding another 25 bp to each of them, beyond the existing one-second and five-basis-point-per-side stress, left +33.79% additive return, +3.68%, +3.09%, and +27.03% chronology blocks, and 6.25% drawdown. An additional 50 bp left the full total positive at +25.28% but made the first block negative. At 100 bp, two blocks were negative and drawdown rose to 17.42%.

This bounds rather than solves deeper-book execution. The candidate is historically robust to a severe 25 bp unsupported-exit surcharge, but actual route and fill evidence is still required. The frozen forward-paper specification now incorporates 5% entry-depth sizing, broker timestamp/fill logging, and explicit exit-depth reconciliation. The latest-twelve-month result remains only +0.20% under the conservative small-account path. The final conclusion remains a modest sparse forward-paper candidate, not a money printer or live-ready system.
