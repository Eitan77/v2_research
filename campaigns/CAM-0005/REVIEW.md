# Campaign Review: CAM-0005

## Decision

Freeze two development sleeves and stop adapting CAM-0005. Neither is a
genuine Type-A candidate.

The unit q60/edge25 reversal is a real, executable recent edge: under
first-ask 15:59 entry, first-bid 09:35 exit, and 5 bp additional slippage per
side, it averaged 6.01% per month over 18 months with 18.07% drawdown. A capped
allocation that sizes low-volume events at 0.25 and SOXS at 0.75 averaged
5.04% with 5.91% drawdown and five-day recovery. Both are adapted development
results, not promotions or holdout evidence.

## Evidence and diagnosis

The mechanism is late-day SMH pressure followed by overnight reversal.
Negative late SMH pressure selects long SOXL; positive pressure selects long
SOXS. A matching signed SMH diagnostic was positive before product leverage,
while QQQ lacked comparable economics. The effect survived q40-q60 magnitude
neighbors, edge20-edge33 pressure, 15:56-15:59 entry latency, next-open through
09:35 exits, both product legs, and 2-10 bp screens.

Execution evidence is unusually complete. Targeted Alpaca SIP pulls covered all
134 frozen events without requesting any date in the sealed interval. Every
event had post-timestamp quotes and trades. Buying the first valid ask after
15:59 and selling the first valid bid after 09:35 reduced the median exit
spread from roughly 15 bp at 09:30 to 4.88 bp. The preferred q60 path remained
positive through 10 bp additional slippage per side.

The headline still fails Type A. At the central 5 bp replay, unit q60 had six
negative months, an unresolved ending drawdown, and 52.92% top-five-day profit
share. Removing its ten best days erased about 88.7% of profit. Circular block
bootstrap left a high drawdown tail even though positive-return frequency was
strong. Causal volume conditioning lowered realized drawdown to about 6%, but
also lowered average monthly return to roughly 5.4%. Capped allocation
improved balance further but could not approach the approximate 10%-15%
reference objective without violating the plan's 1.0 exposure cap.

Older context is the decisive regime warning. From March 2023 through October
2024, the high-volume bar-stage rule averaged only 1.62% per month, suffered
36.24% drawdown, ended unrecovered, and had a losing SOXS leg. The recent
improvement is genuine descriptively, but no causal regime-onset rule was
validated. Shifted 10/20/40-event activation reduced returns and could not
preempt the first inverse loss, so the November 2024 start is not claimed as
prospectively selectable.

## Completeness audit

Alpha, execution, and portfolio construction were separated. The campaign
tested continuation and reversal, QQQ and SMH, unlevered and leveraged
expressions, bull and inverse legs, signal magnitude, final-hour volatility,
volume, close location, QQQ confirmation, same-day trend, prior trend, SMA20
state, causal activation, entry latency, next-open/09:30/09:35 exits, costs,
chronological blocks, monthly paths, bootstrap uncertainty, top-event removal,
older history, volume tiers, signal tiers, and asymmetric inverse allocation.

The invalid adjustment attempt remains preserved and excluded. Clean
readiness used official split/raw daily factors, fixed-base additive P&L, and
completed-minute timing. Every meaningful run was frozen, reconciled, and
retained. No row on or after May 1, 2026 was loaded.

Source fidelity is inapplicable in the paper-replication sense because this was
a mechanism-first campaign with no external source baseline. Its frozen
starting implementation was reproduced before adaptation, including both
direction controls and matching underlying diagnostics.

A careful skeptical quant could still request genuinely prospective evidence,
verified quote-size units, depth/impact modeling, or a new causal regime
observable. Those are confirmation inputs, not omitted in-sample parameter
tests. No available bounded timing, state, leg, cost, or allocation experiment
can bridge the roughly twofold return gap without leverage or historical
selection.

## Three perspectives

**Researcher:** Late-day semiconductor pressure has a coherent overnight
reversal mechanism with excellent quote coverage and a useful high-volume
state. It deserves prospective observation.

**Skeptic:** The result was adapted on one 18-month regime, the latest unit path
is unrecovered, leading days matter, historical drawdown is severe, and no
causal onset rule explains why the recent window should persist.

**Portfolio engineer:** The combined capped allocation is an interesting
lower-drawdown sleeve, but its roughly 5% average month is not Type A and its
capacity is unproven. Allocate no live capital and do not use the sealed
holdout; paper-monitor the frozen rule while research moves elsewhere.

This completes CAM-0005, not the broader Strategy A objective.
