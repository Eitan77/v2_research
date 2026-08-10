# Individual high-frequency extension - 2026-08-10

## Scope and integrity

This extension re-opened every notable SSRN-derived family at the user-requested low-cost execution gate. Strategies remained individual; capital was fixed at 1.0 with no broker margin; P&L was additive; every reported quote result uses marketable 09:40 Alpaca SIP NBBO fills relative to the 09:30 midpoint plus the stated additional adverse bps per side. No data on or after 2026-05-01 was loaded. All results are adapted development evidence, including the May 2025-April 2026 quote window, and are not untouched out-of-sample evidence.

RUN-0032 independently reconstructed and exactly reconciled all seven notable results. RUN-0033 through RUN-0040 then tested overlays, 231 parameter-neighborhood variants at 2/5/10 bps, exact quote fills, concentration, listing history, cadence, time blocks, and redundant exposure.

## Best balanced result

The strongest balance of frequency, profitability, consistency, and concentration is now the S&P 500 MA200 momentum rank with a 0.8 pairwise-correlation cap and a causal three-session average of the 126-minus-21-day momentum score.

| Exact 09:40 quote result | Net additive return | DD | Positive months | Trade-session fraction |
|---|---:|---:|---:|---:|
| Pure marketable quote | +101.34% | 10.71% | 9/12 | 51.8% |
| Quote plus 2 bp/side | +100.75% | 10.76% | 9/12 | 51.8% |
| Quote plus 5 bp/side | +99.87% | 10.83% | 9/12 | 51.8% |

Relative to the original corr-capped quote result (+93.12%, 12.25% DD, 9/12 positive months, 65.7% cadence), three-day smoothing adds 7.64 percentage points and lowers drawdown by 1.49 points while still meeting the preferred every-two-days cadence. The adjacent five-day smoother is slightly stronger (+104.92% at quote plus 2 bp, 10.33% DD) but trades on only 44.2% of sessions, so it is retained as a lower-frequency sensitivity rather than the primary expression. At the full-history bar stage, all 27 smoothing/correlation/breadth variants were profitable at 10 bps; breadth gates consistently diluted the edge.

This is a Pareto improvement in the available development sample, not promotion evidence.

## Other exact-quote findings

| Family | Adaptation at quote +2 bp/side | Net | DD | Positive months | Trade-session fraction | Judgment |
|---|---|---:|---:|---:|---:|---|
| Single MA | MA200 top 10, persistence 3 | +112.41% | 10.72% | 9/12 | 62.2% | Higher return; theme-heavy |
| Dual MA | MA50/200 top 10, persistence 3 | +112.19% | 10.59% | 9/12 | 61.0% | Nearly identical to single MA |
| Corr-capped MA | corr 0.8, score smooth 3 | +100.75% | 10.76% | 9/12 | 51.8% | Best balanced candidate |
| Cluster residual | r5 top 3, persistence 2 | +67.58% | 11.99% | 9/12 | 90.4% | Consistency improved, DD worsened |
| Cluster residual | r10 top 3, no persistence | +58.06% | 14.09% | 8/12 | 100% | Broadest independent P&L |
| Characteristic residual | r5 top 10, persistence 3 | +31.92% | 10.85% | 8/12 | 100% | Positive but top-five dependent |
| True-daily alpha | turnover band 0.20 | +44.19% | 12.82% | 8/12 | 98.8% | No material improvement; cost-sensitive |
| Triple MA | MA10/50/200 top 3 | +145.82% | 13.59% | 10/12 | 30.7% | Rejected as concentrated and too sparse |
| Single MA strict history | MA200 top 5, 252-history, persistence 5 | +110.53% | 13.01% | 10/12 | 31.1% | Survives SNDK control but too sparse/concentrated |

The original baselines remain confirmed: uncapped MA200 +100.13%, dual MA50/200 +96.59%, cluster residual +66.51%, characteristic residual +25.83%, true-daily alpha +43.69%, and triple MA3/10/21 +77.74%, all at exact quote plus 2 bp per side.

## Concentration and redundancy

- SNDK first has prices on 2025-02-13 and enters the point-in-time S&P membership set on 2025-11-28. There are 199 completed prior observations; the membership-date close is observation 200 and any resulting MA200 position is entered the next session. This is causal, but it sits exactly at the native minimum-history boundary.
- Requiring 252 observations reduces the top-five/persistence-five quote return from +129.36% to +110.53%. The edge survives, so SNDK is not the sole explanation.
- The narrow MA versions are nevertheless concentrated. Native top-five/persistence-five has 80.4% of positive symbol P&L in its best five symbols; removing those five leaves +16.82%. The strict-history version falls to 69.7%; removing its best five leaves +27.24%.
- The long-term triple-MA result is not independent: its best five symbols account for 88.2% of positive symbol P&L, and removing them changes +145.82% to -1.98%. It is rejected.
- Single- and dual-MA daily quote P&L correlations are 0.997 for top-10/persistence-three and 0.996 for top-five/persistence-five. They are the same economic trade and must not be counted as two discoveries or combined to manufacture diversification.
- Corr-capped persistence-three retains +36.29% after removing its best five contributors and meets cadence. This is materially healthier than the narrow top-five versions.
- Cluster residual r10/top3 is the least concentrated new candidate: its best five contribute 32.0% of positive symbol P&L and leave-top-five return remains +15.60%. However, return faded from +44.74% in the first six quote months to +13.32% in the last six.
- Characteristic residual leaves only +1.49% after its best five; true-daily alpha and triple MA become negative. Those apparent printers are not robust enough for promotion.

## Execution and adaptation conclusions

- Every profitable candidate selected for execution testing received 100%-coverage exact SIP quote replay at 0, 1, 2, and 5 additional adverse bps per side.
- Hard persistence can increase headline return by holding only names that remain highly ranked, but it reduces breadth and often creates sparse rebalancing. Causal score smoothing is the cleaner corr-capped improvement because it stabilizes ranks without requiring a named stock or a retrospective winner list.
- Five-day score smoothing maximizes quote return and drawdown quality; three-day smoothing is preferred because it restores the requested at-least-every-two-days activity.
- Volatility targeting substantially improves the original short-horizon triple-MA drawdown, but sacrifices most return. Changing to MA10/50/200 restores return only by concentrating on the same recent winners; it does not rescue the family.
- Turnover bands and inverse-volatility sizing do not materially improve true-daily alpha. Its entire neighborhood is unprofitable at 10 bps in the bar model, confirming execution sensitivity.
- Sequential limit-then-cross execution did not improve the original MA200 candidate. Through-price touches remain diagnostics, not queue-proven passive fills.

## Decision

No candidate is promoted and the May 2026 holdout remains sealed. Freeze the corr0.8/three-day-smoothed MA candidate for forward paper testing alongside its unsmoothed corr-capped benchmark. Keep uncapped persistence-three as a higher-return, higher-concentration benchmark and cluster residual as an independent daily watch candidate. Treat the dual MA as redundant, and do not advance the characteristic residual, alpha, triple-MA, pivot, or true-pair candidates without genuinely new information.
