# CAM-0625 review

## 2026-08-10 split-repaired checkpoint

RUN-0001 through RUN-0016 are invalid because the inherited stock panel applied
forward split multipliers in the wrong direction. The current lineage starts with
the 25-family repaired RUN-0020, repair RUN-0021, and quote RUN-0023.

The final equal-weight substitution combines CAM-0600, CAM-0621, CAM-0624, and
CAM-0618. It returns +153.6% additively over the repaired full history with 11.5%
drawdown and 52/27 positive/negative months. The 09:40 SIP replay from 2025-05-01
through 2026-04-30 returns +40.0% after 2 bp additional slippage per side, with
7.25% drawdown and 10/2 months. At 10 bp additional slippage it retains +37.3%.

The three fixed folds are all positive. The final 21-session block bootstrap is
materially less comforting: 5th-percentile one-year return is −4.1%, 1st percentile
is −14.6%, and 95th-percentile drawdown is 21.4%. The frozen pre-2024 family gate
selects only ETF IBS, so the final construction was not historically identifiable.
Two causal regime monitors were rejected because they worsened full-history risk.

This is the best repaired recent-regime development lead, but it is not promoted.
Use unchanged forward paper tracking only; do not access the May 2026 holdout or
deploy capital based on this checkpoint.

The post-checkpoint recent-leader audit blocks the extraordinary standalone
trend results. None of the five largest quote-return families passes the frozen
pre-2024 gate. CAM-0611 and CAM-0612 correlate 0.87 and share SNDK as the top
contributor. Across the five leaders, the top five symbols supply 72% to 91% of
positive quote P&L; removing them leaves as little as +3.5% to +27.8%, except
CAM-0612 at +7.6% despite its +169.6% headline return.

The final four-sleeve ensemble is materially broader: 30 profitable symbols,
nine losing symbols, 13.5% top-symbol share, and 52.1% top-five share. Removing
the top five leaves +18.1% net. A prespecified 10% symbol cap improves recent
drawdown to 6.26% and recent return to +40.7%, but only lowers top-five share to
51.2% and reduces full-history return to +141.5%. It therefore fails the frozen
material-concentration-improvement rule and is not selected as the primary.

RUN-0029 confirms that the result is not wholly dependent on one sleeve, but it
also rejects a strong independence claim. Recent daily sleeve correlations range
from 0.50 to 0.77. Removing CAM-0600 still leaves +29.0% with 5.06% drawdown;
removing any other sleeve leaves +42.3% to +44.5%. The median best-sleeve share
of positive monthly contribution is 59.0%, with two of 11 applicable months above
75%. Preserve equal weight: this is a qualified multi-mechanism result, not a
reason to optimize sleeve weights on the same development window.

The 25-family rank-persistence audit covers 1,459 repaired 2-bp variants. Median
pre-2024 versus 2024-through-April-2026 rank correlation is 0.42; 10 families
are below 0.25 and one is negative. Price momentum's correlation is effectively
zero. At the same time, 22 of 23 reported survivors are positive before 2024 and
their median early rank percentile is 89%. This is evidence for broad mechanism
persistence, not for the exact late-window-selected specification: the survivor
choice remains adapted and the early rank is descriptive, not an untouched test.

RUN-0031 replaces the invalid-lineage displayed-size calculation and handles
Alpaca's documented quote-size unit change: round lots before 2025-11-03 and
shares afterward. Across 1,282 repaired quote roles, 10% of one displayed quote
supports about $21.9k of portfolio capital at p1, $83.9k at p10, and $500.0k at
the median; the minimum is $9.3k. These figures are only a top-of-book warning.
They do not model queues, replenishment, depth, partial fills, or market impact,
so prospective small-order tracking remains mandatory before capital sizing.

## 2026-08-10 checkpoint

CAM-0625 combines four whole mechanisms selected from the completed SSRN
development series: panic-aware S&P momentum, broad S&P multifactor, multi-day
ETF IBS, and volatility-managed safest-distress. The sleeves have modest daily
correlations and no broker margin. The combination is explicitly adapted and
is not a source-faithful strategy from the paper.

Equal weight returned +107.4% additively from 2021-05-03 through 2026-04-30 at
the sleeves' 2-bp paths with 9.0% drawdown. Corrected 09:40 target-change SIP
replay from 2025-05-01 through 2026-04-30 returned +52.6% after 2 bp of
additional adverse slippage per side, with 5.6% drawdown, 10/2 positive/negative
months, and 12.5% of positive P&L from the best five days. At 10 bp additional
slippage, it still returned +49.9%.

The causal inverse-volatility version is smoother: +40.0% in the quote year,
4.3% drawdown, and 11/1 months; at 10 bp it retained +37.5% and 4.4% drawdown.
All leave-one-sleeve-out paths remained profitable. Momentum supplies the most
recent income, multifactor smooths the path, IBS diversifies but is delay- and
cost-sensitive, and distress supplies a low-volatility distinct factor.

The latest 12/18/24-month results are at the 100th historical percentile. The
first of three chronological folds was much weaker than the last. This is a
promising recent-regime development lead, not evidence of a permanent money
printer. The responsive six-month activation monitor remained on throughout
the quote window; it is suitable as future decay governance but did not create
the result.

No May-2026-or-later rows were loaded. The campaign is not promoted. The next
valid step is an unchanged forward paper period with target-change quote
tracking and no additional filters.

The following displayed-NBBO figures belong to the invalid pre-repair lineage
and are superseded by RUN-0031. At 10% of the single
displayed quote size, the 10th-percentile role supported about $1.7k of
portfolio capital, while the median supported about $86.8k. This is neither a
market-impact model nor a capacity estimate. It means live sizing cannot be
inferred from normalized returns and requires small-scale depth/impact paper
tracking first.

Exposure attribution rejects a market-neutral interpretation. Equal weight has
simple SPY beta 0.40 and SMH beta 0.22; causal inverse vol has SPY beta 0.39
and SMH beta 0.20. Multivariate R² is 31%–40%, and the worst 5% of SPY days
cost 42%–50% of fixed capital cumulatively. Positive multivariate intercepts
are encouraging but do not remove the material equity and semiconductor tail.

The broad-market defense test is negative. Prior-close SPY MA100/150/200 gates
all lowered full-history return and failed to reduce drawdown reliably; recent
quote drawdown was unchanged. Do not add this overlay to the frozen ensemble.

The S&P reconstruction matters. A QQQ multifactor substitution preserved most
performance, but substituting QQQ momentum cut full return from +107.4% to
+72.6% and nearly doubled drawdown; substituting both S&P sleeves produced
+67.1% with 20.1% drawdown. Keep the quote-validated S&P core frozen, but treat
its provisional point-in-time membership as a material evidence limitation.

The 21-session block bootstrap is supportive but not risk-free. For simulated
252-session paths, equal weight's 5th-percentile return was +2.5% and
95th-percentile drawdown 13.3%; causal inverse vol was +0.6% and 12.3%. Both
lost roughly 5%–6% at the 1st return percentile. Treat this as a descriptive
path stress, not a probability forecast.
