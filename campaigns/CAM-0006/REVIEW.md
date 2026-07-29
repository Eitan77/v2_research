# Campaign Review: CAM-0006

## Decision

Freeze one execution-qualified development sleeve and close CAM-0006. It is a
real opening-auction absorption edge, but it is not a genuine Type-A Recent
Money Printer.

The frozen rule buys the strongest causal reclaim among point-in-time QQQ
stocks after a bottom-decile negative official opening gap, elevated official
auction size, high prior QQQ volatility, and a liquid/strong completed first
minute. Under first-ask entry after 09:31, first-bid exit near the close, and
2 bp additional slippage per side, it averages 5.53% per month over the
representative 15-month view with 9.83% drawdown and five-day recovery. That
is useful, but roughly half the profile's return reference.

## Evidence and diagnosis

Clean readiness selected official NASDAQ opening auctions by matching Q and O
conditions on venue, price, and size. Point-in-time QQQ membership, raw prior
closes, common-split rejection, shifted liquidity, early-close calendars, and
completed-minute timing produced 44,549 causal signal-complete events through
April 30, 2026. Future exit or minute density never changed the causal rank.
The first fixed-clock readiness and first tail-neighborhood execution were
invalidated, preserved, corrected, and never interpreted.

The frozen baseline tested continuation and absorption, long, protected-short,
and balanced books, four exits, and 0-20 bp costs. Broad portfolios were thin
edge: no 10 bp baseline survived the full recent window. Event diagnosis found
one coherent mechanism—long negative-gap absorption held toward the close.
Gap-tail, reclaim, auction anomaly, and auction-participation neighbors then
formed a positive region, but the best 10 bp cell averaged only about 5% per
month and retained seven losing months.

Timing and risk tests rejected easy rescues. Entry delay from 09:31 to 09:35
reduced return. One-to-three-percent stops worsened risk/return, and exiting
after a completed close recrossed the auction erased the edge. The profitable
paths often retest the auction before recovering. Equal weighting was improved
by selecting the strongest same-day reclaim, while prior high QQQ volatility
lowered drawdown sharply. QQQ trend, breadth, alternative allocation, and
first-minute activity/close-quality filters could improve the sleeve, but no
bounded causal cell reached 6% per month after central execution assumptions.

Execution evidence is complete and deliberately targeted. Alpaca SIP pulls
covered only 232 frozen union events and their entry/exit windows, followed by
12 missing event-phase extensions. All 232 events ultimately had valid quotes
and trades. Eleven entries arrived more than ten seconds after 09:31 and one
exit arrived late; those events use their actual delayed quotes. For the final
94-event sleeve, median entry spread is 28.26 bp, median exit spread 2.99 bp,
and median NBBO return is 18.78 bp below the minute-bar return. Raw quote sizes
remain unverified units, so this is execution evidence without a capacity
claim.

## Robustness and limitation

The final sleeve trades 94 events across 59 symbols. Its three consecutive
six-month returns are 22.38%, 22.55%, and 33.55%. Dollar-participation q50 and
q67 neighbors and 0/2/5 bp slippage neighbors remain positive in every block.
The central sleeve has three negative and three inactive months, a 62.77%
event win rate, 50.10% top-five-day share, and 16.00% top-symbol share.

All planned adversarial removals leave positive total return. Removing the best
five days, however, lowers average month from 5.53% to 2.90%; removing the top
five symbols lowers it to 2.03%. Twenty thousand seeded circular three-month
block samples are positive 99.79% of the time, but the median average month is
only 4.34%, the 95th percentile is 7.30%, and only 0.05% reach 10%. This is
strong evidence for a modest edge and strong evidence against a Type-A claim.

## Completeness audit

The mechanism-first baseline was reproduced before adaptation. Alpha,
execution, risk, and portfolio construction were separated. The campaign
tested official auction selection, gap magnitude/sign, auction anomaly and
participation, first-minute reclaim/activity/range/VWAP, shifted liquidity,
QQQ trend and volatility, signal breadth, continuation and absorption,
long/protected-short/balanced legs, 09:31-09:35 entries, four same-session
exits, fixed stops, mechanism exits, equal and causal top-event allocation,
5-20 bp bar costs, marketable NBBO, added slippage, months, blocks, symbols,
events, leave-outs, parameter neighbors, and moving-block uncertainty.

Direct shorts were intraday, protected, and forcibly closed; they failed and
were rejected. Fixed-capital additive accounting and gross exposure at or
below 1.0 were used throughout. Every meaningful run was frozen and
reconciled; failed and invalid attempts remain visible. No row beginning
May 1, 2026 was requested or loaded.

Source fidelity in the paper-replication sense is inapplicable because CAM-0006
was mechanism-first. The frozen internal source contract—the official auction,
completed first minute, point-in-time universe, both mechanism signs, and
short safeguards—was implemented faithfully.

A skeptical quant could still request prospective paper evidence, verified
quote-size units, or depth/impact modeling. Those are confirmation inputs, not
missing in-sample tests. No obvious bounded causal experiment remains that can
bridge the roughly twofold return gap without leverage, retrospective ticker
selection, or a new mechanism.

## Three perspectives

**Researcher:** Official opening-auction inventory pressure followed by a
strong first-minute reclaim is a coherent, distributed, executable long edge,
especially in high prior market volatility.

**Skeptic:** The sleeve is heavily adapted, inactive in three months,
contributor-dependent, exposed to wide opening spreads, and has no untouched
or prospective confirmation. Its return is nowhere near Type A.

**Portfolio engineer:** The sub-10% drawdown and short recovery make it an
interesting paper-observation sleeve. Allocate no live capital and do not open
the sealed holdout. Monitor the frozen q50/q67 neighborhood while the active
Strategy A search moves to a different information event and payoff horizon.

CAM-0006 is complete; the broader Strategy A objective is not.
