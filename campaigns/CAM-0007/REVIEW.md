# Campaign Review: CAM-0007

## Decision

Retire CAM-0007. Earnings announcements produce a genuine, causally tradable
long event premium, but not a Type-A Recent Money Printer.

The strongest development-selected combined profile averages 3.16% per month
over the representative 15 months at 10 bp per side, with 14.43% drawdown,
101-day recovery, and 11 positive versus four negative months. At 20 bp per
side it still averages 2.86%, and a five-minute entry delay leaves 2.96%.
Those are useful robustness facts, but the return is far below the 10%-15%
reference objective.

## Source and readiness

The campaign built a public earnings registry from local yfinance timestamps
and a conservative explicit-release detector validated against their overlap.
Apparent precision was 97.76% and local-ground-truth recall 88.22%. After
timestamp clustering, session mapping, during-session exclusion, and
point-in-time QQQ membership, 742 events across 113 symbols remained.

Alpaca requests were narrow: missing pre-cutoff event news and split-adjusted
daily bars for only those 113 registry symbols. A provider response containing
three older out-of-window rows was preserved as invalid, then filtered to the
explicit local-time boundary. Market readiness found 728 exact 09:30/09:59/
10:00 signals and 720 shifted-liquidity-eligible events. No row on or after
May 1, 2026 was loaded.

## Runs and diagnosis

RUN-0001 reproduced the frozen four-leg baseline before adaptation. Among 536
absolute-gap events, positive continuation, negative failure, negative
continuation shorts, and positive-failure shorts were balanced in count.
Protected intraday shorts lost. The best 10 bp baseline—positive-gap
continuation to the five-session close—averaged only 0.864% per month.

RUN-0002 tested 522 causal gap, reaction, participation, close-location,
announcement, volatility, market-state, horizon, cost, and sizing neighbors.
Two coherent long mechanisms emerged. Positive confirmed gaps drifted most
after after-close releases. Negative gaps that reclaimed during the first 30
minutes recovered most when prior stock volatility was high. At the standard
20% cap each sleeve remained around 1%-1.5% per month.

RUN-0003 tested 1,296 entry, exit, cost, cap, and combined-book variants with
daily marked-to-market P&L and actual overlapping capacity. One-to-five-minute
entry delay did not erase the effect. The positive sleeve formed a broad
seven-to-nine-session plateau; the negative sleeve peaked near eight sessions.
Combining them removed inactive months and reached 3.16% per month at cap50.

RUN-0004 performed the omitted p7-p9/n7-p9 lattice and adversarial allocation
tests. It did not improve the earlier result. Raising equal allocation from
cap50 to cap100 reduced recent average return from about 2.9% to 1.5% and
raised drawdown to 34.8%. Strength-priority cap100 produced only 0.6% recent
average return, negative full-period average return, and 36.8% drawdown.
Concentration was scaling noise and lost opportunity, not exposing hidden
alpha.

RUN-0005 exactly reproduced three frozen profiles, reran 414 capacity-aware
leave-one-symbol/event cases, and performed 20,000 circular three-month-block
samples for each full and recent sequence. Every worst removal stayed
positive, so the edge is not a single-name artifact. Yet zero recent bootstrap
sample for any profile reached a 10% average month. Recent 95th percentiles
were only 4.25%-5.14%.

## Completeness and execution

The campaign separated event alpha, opening reaction, execution timing,
portfolio construction, and risk. It tested both event signs, continuation
and failure, long and protected intraday-short controls, announcement buckets,
gap/reaction thresholds, participation, range close, stock and QQQ states,
same-day through ten-session exits, 10:00-10:05 entries, 5-20 bp costs,
10%-100% position caps, shared, strength, and reserved allocation, monthly and
chronological stability, symbols, events, leave-outs, and bootstrap
uncertainty.

Quote replay is deliberately inapplicable. Even generous bar-stage evidence
is roughly one-third of the lower Type-A reference. Marketable quotes can
reduce or validate that estimate, not create missing alpha. Spending targeted
quote data to rescue it would violate the research workflow.

## Three perspectives

**Researcher:** Earnings reactions contain two coherent long effects: delayed
drift after confirmed positive after-close gaps, and finite recovery after
high-volatility negative gaps reclaim early.

**Skeptic:** Both rules were selected on the same development sample, the best
book has four losing recent months and a negative first block, cap50 is
concentrated, and no bootstrap path approaches Type A.

**Portfolio engineer:** The combined book is a plausible lower-return research
ingredient, not a deployable candidate. Gross is controlled and latency/cost
neighbors are positive, but recovery is too slow and depth/capacity is
unverified. No live capital, holdout access, or quote replay is justified.

CAM-0007 is complete. The broader Strategy A objective remains active.
