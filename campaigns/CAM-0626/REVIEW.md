# CAM-0626 retirement review

## Conclusion

Retire this mechanism without accessing the sealed holdout. None of the tested
expressions is a credible consistent intraday printer after executable SIP
quotes. `promotion_ready` is false.

## Evidence

The source-faithful demeaned-return portfolio was negative before costs. Timing,
selectivity, inverse-variance weights, beta-neutral weights, short-only leader
reversal, sector ETFs, passive entries, five stop widths, and four capacity
levels were then tested as weakness-driven adaptations.

The best bar-stage candidate, five-minute formation with a 15:50 exit and
inverse-variance weights, showed +2.95% at two basis points per side. Its exact
marketable SIP replay instead lost 23.77% at observed quotes and 43.57% after
two additional basis points per side. It produced only 7 positive months out of
24. This is an execution failure, not a small parameter miss.

The strict queue-backed passive short-leader variant was the sole positive
quote-level full-period cell at two basis points per side, +1.03%. It suffered a
25.87% drawdown, a -10.51% worst month, and changed from +21.70% in the early
12 months to -20.67% in the late 12 months. Its five largest positive symbol
contributors supplied 67% of positive contribution. Alternative stop widths
all lost in the late period. Capacity-aware replay reduced full-fill rates from
62.87% at $1,000 to 33.53% at $100,000, and every tested notional lost 20.13% to
22.77% in the recent 12 months.

## Integrity audit

- Every position was cash-collateralized within gross exposure 1.0, sized from
  fixed original capital, stopped, and forcibly closed intraday.
- Stop exits use raw SIP one-minute paths and only request a quote after the
  crossing minute completes. Earlier noncausal stop and capacity attempts are
  preserved and explicitly invalidated.
- Exact quote packages require every intended leg. Passive fills require
  displayed-size queue consumption by same-price SIP prints; cancellations earn
  no credit.
- All loaders and requests end no later than 2026-04-30. No holdout row was
  loaded.
- Failed variants, period instability, concentration, and capacity attrition
  are preserved in the run records and artifacts.

## Why further tuning is not justified

The edge disappears at observed marketable spreads across stocks and sector
ETFs. The only positive passive cell is isolated to one stop, decays completely
in the later half, and is not robust to size. Additional thresholds or ticker
filters would therefore select historical noise rather than repair a stable
economic mechanism. The next informative direction is a genuinely quote-native
relative-value mechanism with lower fundamental divergence risk, not another
filter on this campaign.
