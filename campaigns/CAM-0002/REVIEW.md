# Campaign Review: CAM-0002

## Decision

Retire this extreme intraday sell-shock reversal mechanism for Strategy A.
Do not promote or forward-test its adapted leader. Preserve all ten runs and
continue the broader Strategy A search in a materially different campaign.

## Evidence

The source-faithful recent replication failed: its 60-minute exit lost 21.61%
over 18 months with 33.17% drawdown; the 30-minute exit lost 47.48%.
Threshold tightening and 1/2/5-minute latency did not rescue it.

The campaign tested 10-120-minute formation and exit clocks, raw versus
SPY-residual shocks, clusters, causal abnormal volume, stocks versus ETF
products, reclaim/no-new-low confirmations, more frequent 2%-4% shocks, and
2%-6% stop/target overlays. It recorded 1,689 parameter cells plus a dedicated
event diagnostic.

The best adapted result waited for a one-minute reclaim after an extreme
15-minute idiosyncratic stock shock, then used a 4% target or 60-minute time
exit. It earned 21.36% over 18 months, averaged 1.58% per month over the
representative 15 months and 1.29% over 12 months, with 2.33% drawdown and
91-day recovery.

## Why it is not Strategy A

The apparent consistency is inactivity: only 13 trades across 12 symbols,
eight of 15 representative months and seven of 12 recent months at zero, and
a zero median month. The top five trades supplied 74.5% of positive
contribution. Less-extreme shocks raised counts into the hundreds but diluted
or reversed expectancy. Frequency and edge did not coexist.

The 45-minute raw-shock winners were dominated by April 7, 2025. Attractive
ETF subsets had only two or three events. Neither is causally selectable.
Leverage or winning-ticker selection would disguise the structural income gap.

## Execution

Runs used next-actionable raw SIP minute opens, 10 bps per side,
regular-session boundaries, and adverse stop-first ordering. Three high-price
rows required conservative fallback in the full risk grid, one in the leader.
SIP quotes/trades were not spent: the best bar result is roughly one-eighth of
the objective and only 13 events deep, so execution replay cannot create the
missing alpha or frequency.

## Three perspectives

**Researcher:** Completed-bar reclaim after an extreme idiosyncratic sell shock
is directionally useful, but only at rare severity.

**Skeptic:** The leader follows a large adaptive family, has 13 events, a
zero-median monthly path, and 74.5% top-five concentration.

**Portfolio engineer:** Low drawdown reflects low utilization, not dependable
income. Do not allocate or lever.

There is no activation contract because nothing is approved. Reopening
requires genuinely new causal information, not parameter retuning. The sealed
interval beginning 2026-05-01 was never accessed. This retires CAM-0002 only,
not the broader Strategy A profile.
