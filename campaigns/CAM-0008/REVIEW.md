# Campaign Review: CAM-0008

## Decision

Retire CAM-0008. Public analyst revisions contain a genuine and distributed
long event premium, especially when price rejects a negative revision, but the
mechanism is not a Type-A Recent Money Printer.

The strongest stable profile averages 2.63% per month over the representative
15 months at 10 bp per side, with 17.70% drawdown, 34-day recovery, and 11
positive versus four negative months. At 20 bp per side it retains 2.28%.

## Source and readiness

The campaign detected only explicit upgrades, downgrades, directional
initiations, and maintained/reiterated price-target changes. A frozen audit
confirmed 90 of 90 detected actions and correctly rejected 40 of 40 near
misses. Precision is supported; recall was not claimed.

Remote retrieval was narrow: 49,472 usable news rows for the 118-symbol
point-in-time QQQ union and 57,239 split-adjusted daily rows for the resulting
117 event symbols. Consolidation produced 7,641 episodes. Exact readiness
loaded 1,917,936 event-minute rows, found 7,116 complete five-minute signals
and 7,094 shifted-liquidity-eligible events. No row on or after May 1, 2026 was
loaded.

## Runs and diagnosis

RUN-0001 reproduced 174 baseline variants. Confirmed long legs averaged 1.74%
per recent month at 10 bp; every protected intraday short portfolio lost.

RUN-0002 tested 720 action, clock, reaction, participation, gap, repetition,
stock/QQQ state, horizon, and cost variants. No screen reached 1.8% per month
at the standard 2% event cap.

RUN-0003 tested 336 cap, overlap, screen, horizon, and cost variants. The
highest recent average was 2.83% but breached 20% drawdown and lost in the
first block. No cap variant reached 3%.

RUN-0004 rebuilt eligibility for completed 1/5/15/30-minute reactions and
0/1/3/5-minute entry delays across 576 portfolios. All 36 comparable parent
cells reproduced exactly. Failed-negative timing neighborhoods remained near
2.6%; a combined 15-minute window reached 2.67% but had six losing months.

RUN-0005 exactly reproduced three profiles, reran 439 capacity-aware
symbol/firm/event/day removals, and generated 20,000 block samples per
profile/window. Every worst removal stayed positive, but zero recent bootstrap
sample reached a 10% average month.

## Completeness and execution

The campaign covered action types and signs, continuation/failure,
protected-short controls, earnings confounds, event clocks, reaction and
attention features, gaps, firm repetition, stock/market states, same-day
through ten-session exits, reaction windows and latency, 5/10/20 bp costs,
2%-10% caps, overlap, periods, contributors, concentration, and removals.

Quote replay is inapplicable. It can validate or lower a 2.6% bar-stage
estimate; it cannot create the missing 7%-12% monthly alpha. No capacity claim
is made.

## Three perspectives

**Researcher:** Negative analyst actions that price rejects contain a real
ten-session resilience premium across fast and slow confirmation windows.

**Skeptic:** The rules are development-selected, four recent months lose,
drawdown is near the profile ceiling, recall is unknown, and no bootstrap path
approaches Type A.

**Portfolio engineer:** The rule may be a lower-return event ingredient, not
deployable evidence. Quotes, impact, and prospective behavior remain
unverified. No live capital or holdout access is justified.

CAM-0008 is complete. The broader Strategy A objective remains active.
