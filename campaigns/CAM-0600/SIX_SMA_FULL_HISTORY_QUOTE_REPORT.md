# Six SMA candidates - full-history quote replay

## Contract and coverage

All six frozen SMA candidates were replayed from the beginning of their available causal panels through 2026-04-30. QQQ histories begin 2019-06-21; S&P histories begin 2021-05-03. Every target change uses the first valid Alpaca SIP NBBO at or after 09:30 as its reference and the first valid marketable SIP NBBO at or after 09:40 as its execution, plus the stated additional adverse bp per side. Positions retain the original next-session lag, fixed-base additive accounting, overnight holding, maximum gross exposure of 1.0, and no broker margin.

This is a target-change quote replay: daily holding P&L remains the repaired split-adjusted open-to-next-open bar return, while every portfolio change is repriced to the observed quote. It is not a synthetic assumption that every holding is liquidated and repurchased daily.

Coverage is 100% across 3,310 candidate-tagged fill rows. There are 3,308 normal 09:40 fills and two candidate rows for one XLNX corporate-action exit. XLNX had no quote on its scheduled 2022-02-14 exit because it was no longer trading; both affected QQQ strategies were conservatively exited using the last valid SIP bid before the 2022-02-11 close, referenced to that session's 09:30 midpoint. The scheduled and actual timestamps are both retained in the fill ledger.

For ordinary fills, median execution delay after 09:40 was 0.11 seconds, the 99th percentile was 23.8 seconds, and the maximum was 553 seconds during a market-wide halt. No holdout rows were loaded.

## Full-history results at quote plus 2 bp per side

| Candidate | History | Net additive return | Maximum DD | Positive / negative months | Worst month | Top-five positive share | Leave top five |
|---|---|---:|---:|---:|---:|---:|---:|
| QQQ dual MA50/200, weekly top 3 | 2019-06 to 2026-04 | **+362.8%** | **17.5%** | 48 / 25 | -16.6% | 53.2% | +119.1% |
| QQQ single MA150, weekly top 3 | 2019-06 to 2026-04 | +344.7% | 40.8% | 48 / 28 | -19.7% | 56.5% | +94.1% |
| QQQ triple MA10/50/200, monthly top 3 | 2019-06 to 2026-04 | +258.2% | 33.4% | 44 / 28 | -18.8% | 51.0% | +70.3% |
| S&P dual MA50/200, weekly top 3 | 2021-05 to 2026-04 | +248.4% | 25.9% | 35 / 16 | -23.6% | 57.2% | +40.1% |
| S&P triple MA10/50/200, monthly top 3 | 2021-05 to 2026-04 | +210.0% | 30.2% | 32 / 18 | -25.5% | 67.3% | +7.9% |
| S&P daily MA200 corr0.8, smooth 3, top 10 | 2021-05 to 2026-04 | +136.0% | 26.7% | 31 / 20 | -13.9% | **31.0%** | **+67.6%** |

All six remain profitable at quote plus 10 adverse bp per side: +352.6%, +333.1%, +251.8%, +240.3%, +205.2%, and +121.8%, respectively.

## Latest 12 months under the same full-history replay

| Candidate | Latest 12m net | DD | Positive months | Worst month |
|---|---:|---:|---:|---:|
| S&P triple MA | **+169.6%** | 12.6% | 11/12 | **-1.9%** |
| S&P dual MA | +161.5% | 11.9% | 11/12 | -6.8% |
| QQQ dual MA | +117.1% | 12.9% | 9/12 | -11.9% |
| QQQ single MA | +116.8% | 12.9% | 9/12 | -11.9% |
| S&P daily corr-capped MA200 | +100.8% | **10.8%** | 9/12 | -5.7% |
| QQQ triple MA | +90.4% | 16.4% | 8/12 | -9.3% |

## Judgment

- **QQQ dual MA is the strongest full-history result.** It beats QQQ single MA in return and cuts full-history drawdown from 40.8% to 17.5%, while retaining +119.1% after removing its five best contributors. Recent behavior remains almost identical to QQQ single MA, so both should not be counted as independent edges.
- **S&P triple MA remains the best recent printer but the weakest concentrated historical claim.** Full-history worst month is -25.5%, drawdown is 30.2%, and removing its five largest contributors leaves only +7.9%.
- **S&P dual MA is more defensible than S&P triple MA historically.** It has lower concentration and +40.1% after removing its five best contributors, although the latest year still accounts for most of its result.
- **The daily corr-capped MA has the healthiest contributor breadth.** Its headline return is lowest, but its top-five share is only 31.0% and it retains +67.6% after removing them.
- **QQQ triple MA is not competitive.** It suffered -57.6% additive P&L in 2022, and its exact recent quote return trails the other weekly QQQ expressions.

The six remain development candidates, not six independent discoveries. No candidate is promoted and the May 2026 holdout remains sealed.
