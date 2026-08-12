# CAM-0627 retirement review

## Conclusion

Retire same-index ETF convergence without opening the sealed holdout.
`promotion_ready` is false.

## What was tested

The campaign began with the source mechanism: buy the cheaper ETF, short the
richer ETF, and exit on executable convergence. Because current ETF share
prices differ, the implementation disclosed and used a causal log-price-ratio
anchor. A 600-variant one-minute screen covered SPY/IVV, SPY/VOO, IVV/VOO, and
QQQ/QQQM. Quote-native replay then synchronized 17.44 million SIP quotes into
50 ms causal snapshots across opening, midday, and closing windows on the first
complete session of each month from May 2024 through April 2026.

No pair generated an executable deviation of 1 bp in the quote sample. Maximum
observed deviations were 0.27 to 0.73 bp. Sub-basis-point SPY/IVV tests yielded
only four positive marketable trades and +0.0065% total before extra slippage;
all cells lost with one extra basis point per side. Across the other three
pairs, every active cell lost even at observed bid/ask fills.

The superficially extraordinary result—+9.14%, 505 wins, and no losses—required
granting one favorable basis point per side on every fill. It was not treated as
execution evidence. A strict one-passive-leg replay used 931,366 SIP prints,
required displayed queue consumption, gave no cancellation credit, hedged only
after a confirmed full fill, and used a $10,000 gross package. Every cell lost.
The most active strict cell averaged -0.40 bp with a 2.8% win rate. Treating
quote sizes as shares rather than round lots, a deliberately favorable schema
sensitivity, also left every cell negative.

## Integrity and conclusion audit

- Gross exposure was capped at 1.0 with half capital per leg and additive
  fixed-base P&L.
- Positions never overlapped within a pair, had frozen pair-P&L stops, and were
  liquidated within the five-minute sample window.
- Signals used completed causal snapshots and both legs had to meet the frozen
  quote-age bound. Marketable entries and exits crossed the observed SIP quote.
- The oversized three-full-month replay was blocked before execution after a
  density probe; it was not silently downsampled. RUN-0003 froze a distinct
  bounded sample before retrieving it.
- All data end no later than 2026-04-30. The sealed holdout was not accessed.
- Remaining pair, threshold, horizon, stop, quote-age, favorable-fill, passive
  queue, and quote-size questions were answered. Additional tuning would mine
  noise below the two-leg spread rather than repair the mechanism.
