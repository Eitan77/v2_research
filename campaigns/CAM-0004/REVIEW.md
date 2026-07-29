# Campaign Review: CAM-0004

## Decision

Retire the adapted QQQ proxy branch as economically weak for Strategy A. Do
not promote, forward-test, or spend quote-replay budget. Preserve all six runs
and begin a materially different campaign.

Exact replication of Brogaard, Han, and Kim remains blocked rather than
failed. The declared workspace lacks the paper's point-in-time S&P 500 June
membership, exact 15 lagged characteristics, and TAQ-equivalent adjusted
midpoints. The source contract was recovered faithfully, but it was never
silently replaced with the proxy implementation.

## Evidence and diagnosis

The unconditional adapted mechanism has the wrong gross sign. Every RUN-0002
baseline lost, and all 60 RUN-0003 combinations lost even at source-close
marks. Following-open latency, the emergency stop, one-period holding, and
decile breadth therefore do not explain the failure.

The source-prespecified stress and cumulative-formation tests did uncover a
coherent conditional effect. K6/M2-M4 reversal became positive when lagged
market-wide volatility was unusually high, and early/late chronological halves
were both positive. That result is real enough to preserve as knowledge, but
not large enough to promote. Its break-even marketable cost was roughly one
basis point per side and its representative gross month was below 0.8%.

The executable screen showed that the edge is entirely long-low. Every
protected short branch lost. After dividing overlapping M-period cohorts by M,
the best 1 bp/side long-only branch averaged 0.89% per month; at 3 bp it
averaged 0.44%. A four-model residual challenge raised the best 1 bp result
only to 0.95% per month. Five recent months were negative, drawdown was 6.46%,
and the top five profitable days exceeded total profit because the other days
netted negative.

## Completeness audit

Alpha, execution, and portfolio construction were separated. The campaign
tested source versus actionable marks, one- through six-period holdings,
cumulative K/M formation, decile/quintile breadth, long and protected-short
legs, causal noise/volatility states, equal and capped-strength weights,
concurrent-capital scaling, costs, and four bounded residual control sets.
Monthly, chronological, clock-period, drawdown/recovery, and top-day
concentration evidence were inspected. No close bar-stage candidate remained,
so SIP quote replay would be resource waste rather than a missing rescue.

The exact source universe/model remains an input blocker and is not included in
the retirement claim. Within the data that passed readiness, a careful skeptic
has no obvious model-, horizon-, state-, leg-, cost-, or portfolio-level test
capable of closing the roughly tenfold gap to the Type-A objective without
historical subset selection or leverage.

## Three perspectives

**Researcher:** Liquidity-stress conditioning recovers a small cumulative
loser rebound, but the broad recent residual-reversal relationship is absent.

**Skeptic:** The exact paper was not replicated, the short leg fails, and the
surviving long-only profit is sparse, cost-fragile, and top-day dependent.

**Portfolio engineer:** The bar-stage payoff cannot justify quote work or
capital. Allocate nothing and move to a different mechanism.

The sealed interval beginning May 1, 2026 was never accessed. This retires only
CAM-0004's adapted branch, not the broader Strategy A profile.
