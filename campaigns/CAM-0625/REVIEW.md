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

The displayed-NBBO capacity diagnostic is a warning: at 10% of the single
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
