# Low-frequency survivor audit - 2026-08-10

## Scope

This is a frozen audit of the exact ten highest-return low-frequency survivors previously shown to the user. No strategy was changed and no new winner was selected. Historical results use repaired split-adjusted daily bars at 2 bp per side. Recent results use complete-coverage marketable Alpaca SIP NBBO fills at 09:40, measured against the 09:30 midpoint, plus 2 adverse bp per side. Capital is fixed at 1.0, P&L is additive and noncompounded, broker margin is not used, and no data on or after 2026-05-01 was loaded.

These strategies hold their selected long portfolio overnight and generally remain invested between scheduled rankings. “Monthly” and “weekly” mean the ranking is deliberately refreshed on that schedule; the conditions are not merely rare daily signals. Quote roles are buy or sell target changes, not holding days.

## Last 12 months versus history

| Family | Schedule | Exact quote last 12m | DD | Positive months | Worst month | Full-history bar net | Pre-last-12m bar net | Recent share of all profit |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Triple MA | Monthly | +169.6% | 12.6% | 11/12 | -1.9% | +214.1% | +44.2% | 79.4% |
| Dual MA | Weekly | +161.5% | 11.9% | 11/12 | -6.8% | +250.0% | +89.3% | 64.3% |
| Single MA | Weekly | +116.8% | 12.9% | 9/12 | -11.9% | +349.0% | +232.7% | 33.3% |
| Distress quality | Monthly | +76.2% | 9.7% | 9/12 | -3.1% | +196.2% | +118.4% | 39.6% |
| Price momentum | Monthly | +73.0% | 13.5% | 9/12 | -10.2% | +235.4% | +149.4% | 36.5% |
| Value quality | Monthly | +64.3% | 6.3% | 9/12 | -3.4% | +138.7% | +74.2% | 46.5% |
| ETF alpha combo | Monthly | +64.3% | 15.7% | 9/12 | -10.3% | +196.0% | +137.1% | 30.0% |
| Cluster residual | Monthly | +44.7% | 21.0% | 8/12 | -8.0% | +210.7% | +164.8% | 21.8% |
| Sector momentum + MA | Monthly | +39.5% | 10.0% | 8/12 | -6.4% | +130.0% | +93.1% | 28.4% |
| Residual momentum | Monthly | +37.3% | 8.7% | 9/12 | -6.8% | +52.7% | +12.8% | 75.7% |

The exact quote results remain positive even with 10 additional adverse bp per side. This is expected for low-turnover portfolios and confirms that the headline returns are not a 1–2 bp fill artifact. It does not validate the strategy-selection process out of sample.

## Reality checks

| Family | Positive rolling 12m windows | Latest 12m percentile | Top-five share of positive P&L | Return after removing top five | Parameter grid positive at 10 bp | Assessment |
|---|---:|---:|---:|---:|---:|---|
| Triple MA | 87.3% | 100.0% | 66.5% | +12.8% | 79.2% | Executable, but latest year and a few stocks dominate |
| Dual MA | 96.2% | 99.7% | 56.8% | +39.9% | 93.1% | Stronger historical and neighborhood support than triple |
| Single MA | 84.9% | 98.4% | 56.2% | +99.4% | 98.6% | Best long-history support among the MA variants |
| Distress quality | 81.1% | 100.0% | 49.4% | +79.2% | 100.0% | Broadly credible, though the latest year is still exceptional |
| Price momentum | 79.6% | 97.5% | 61.0% | +50.4% | 100.0% | Established edge with meaningful winner dependence |
| Value quality | 81.2% | 100.0% | 54.4% | +46.4% | Best recent drawdown and an even first/second-half path |
| ETF alpha combo | 91.3% | 95.2% | 72.8% | +30.6% | Historical profit exists, but family-grid fragility is serious |
| Cluster residual | 84.7% | 79.0% | 31.8% | +127.6% | Broadest contributor base; recent performance is fading |
| Sector momentum + MA | 82.7% | 84.1% | 94.1% | -6.2% | Top-one sector design is intrinsically concentrated |
| Residual momentum | 43.1% | 99.3% | 38.6% | +25.0% | Primarily a new regime, not a historically consistent printer |

“Parameter grid” is the already-frozen campaign grid, evaluated at 10 bp per side. It is a robustness diagnostic, not a correction for selecting the best member of that grid.

## Time-path findings

- Triple MA accelerated from +42.2% in the first six quote months to +127.4% in the second six. Its largest full-history contributors are SNDK, PLTR, WDC, NVDA, and MU. The fill is real; the generality is not yet established.
- Dual MA accelerated from +45.1% to +116.4%, but entered the recent year with twice as much prior profit as triple MA. It owns nearly the same recent stocks, and its daily quote P&L correlation with triple MA is 0.948.
- Single MA is the most historically established of the three: +232.7% before the latest year and +99.4% after removing its five best full-history contributors. It still suffered a negative 2022.
- Value quality is unusually balanced within the recent year: +32.3% in the first six months and +32.0% in the second, with only 6.3% quote drawdown. That is the cleanest recent path in this group.
- ETF alpha combo faded from +51.7% to +12.6%; only 25% of its campaign grid remained profitable at 10 bp. It should not be treated as a stable current printer.
- Cluster residual faded from +35.7% to +9.1%, but its full-history contributor breadth is the best in the group. It looks more like a real but currently weak edge than a recent printer.
- Residual momentum improved from +15.6% to +21.7%, but 75.7% of all historical profit arrived in the latest year and fewer than half of rolling 12-month windows were positive. It is a tactical recent-regime candidate only.
- Sector momentum’s top-five concentration is not directly comparable with stock strategies because it intentionally selects one of only eleven sector ETFs. Even with that allowance, removing the five profitable sectors makes the historical result negative.

## Conclusion

The quote fills are real under the frozen replay contract; the strongest claims are not all equally real statistically.

1. **Best supported historically:** single MA, dual MA, distress quality, price momentum, and value quality.
2. **Historically broad but recently fading:** cluster residual.
3. **Recent tactical printers requiring prospective confirmation:** triple MA and residual momentum.
4. **Do not advance unchanged:** ETF alpha combo because of fade and grid fragility; sector momentum + MA because almost all profit is carried by a few sector choices.

Triple MA and dual MA should not be combined as diversification. If only one were advanced, dual MA has the better evidence: more pre-recent profit, more positive rolling windows, a broader profitable parameter neighborhood, and less top-contributor dependence. Single MA is the stronger long-history benchmark and should accompany it in any prospective paper test.

All evidence remains adapted development evidence. The May 2026 holdout remains sealed and `promotion_ready=false` for every candidate.
