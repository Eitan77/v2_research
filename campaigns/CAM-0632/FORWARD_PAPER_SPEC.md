# CAM-0632 frozen forward-paper specification

## Status and scope

This is an adapted research candidate, not a live-trading recommendation and not untouched out-of-sample evidence. The discovery dataset ends April 30, 2026. Data from May 1, 2026 onward was not accessed. Forward observation starts only after this specification is frozen; no sealed historical rows may be backfilled into the decision.

The canonical signal implementation is `src/frozen_candidate.py`. RUN-0011 independently reproduced all 314 saved discovery trades with zero key mismatches and sub-`1e-16` maximum return difference. Any future runner must call or match that implementation; prose must not be used to reinterpret the rule.

The portfolio is cash-only, long-only, additive, and noncompounding. It has two permanent sleeves, each targeted to 0.50 of the original normalized capital base. Idle sleeve cash is not reassigned. Actual whole-share order quantity is the smaller of `(0.50 * frozen account base) / current ask` and `0.05 * current SIP ask-size units * 100`, rounded down. Simultaneous gross exposure therefore cannot exceed 1.0, and thin displayed depth leaves part of a sleeve in cash.

## Shared data and execution contract

- Use regular-session one-minute raw SIP bars. A bar timestamp denotes its start; its open-to-close return is knowable only after that minute completes.
- Eligible signal-bar start times are 09:35 through 15:35 America/New_York.
- Submit a marketable buy only after all required completed bars exist. Record decision, submission, acknowledgement, and fill timestamps plus contemporaneous SIP NBBO.
- The historical primary model used the first SIP ask at or after decision time plus 250 ms and the first SIP bid at or after exit time plus 250 ms, then charged another 2 bp adverse slippage per side. The adverse stress used 1,000 ms and 5 bp per side.
- Paper performance must use actual simulated-broker fills, not quote touches. Reject or flag observations whose acknowledgement latency exceeds one second, whose quote role is missing, or whose fill cannot be reconciled to the contemporaneous NBBO.
- Record whether the eventual exit quantity exceeds 5% of displayed bid size. Historical causal sizing supported only 73.2% of later exits for a $2,000 account at this fraction; actual broker slippage, partial fills, and time-to-completion are therefore mandatory evidence, not bookkeeping details.
- Each sleeve may hold at most one position. Do not overlap signals within a sleeve. There are no direct shorts, broker margin, or overnight positions.
- The tested rules have no optimized price stop. Do not add one and call the result the same strategy. Operational emergency liquidation before the regular close remains mandatory.

## QQQ leveraged-ETF reversal sleeve

1. For completed QQQ bar `t`, compute `r = close[t] / open[t] - 1`.
2. Require `abs(r) >= 0.0040`.
3. Wait for QQQ bar `t+1` to complete. Require its open-to-close return to have the opposite sign from `r`.
4. If `r > 0`, buy SQQQ; if `r < 0`, buy TQQQ.
5. Enter at the first executable ask after the start of bar `t+2` plus observed decision/broker latency.
6. Hold fifteen one-minute bars, including the entry bar. Exit marketably at the first executable bid after the end of the fifteenth bar.

## SMH leveraged-ETF overshoot-reversal sleeve

1. For completed bar `t`, compute same-minute open-to-close returns for SMH, SOXL, and SOXS.
2. Require `abs(r_SMH) >= 0.0050`.
3. If `r_SMH > 0`, require `r_SOXL - 3*r_SMH >= 0.0020`, then buy SOXS.
4. If `r_SMH < 0`, require `r_SOXS + 3*r_SMH >= 0.0020`, then buy SOXL.
5. Enter at the first executable ask after the start of bar `t+1` plus observed decision/broker latency.
6. Hold twenty one-minute bars, including the entry bar. Exit marketably at the first executable bid after the end of the twentieth bar.

## Frozen monitoring and decision gate

Report every week and month, including zeros: additive net return, trades, active and green days, spread and slippage, acknowledgement latency, missing roles, maximum drawdown and recovery, symbol/family contribution, top-event concentration, and any overlap. Preserve every failed or rejected signal and all broker messages.

Do not change thresholds, holds, confirmation, overshoot definition, sleeve weights, entry, or exit during the observation. Any such change is a new adapted version and resets the forward clock.

The minimum review point is the later of twelve calendar months or fifty completed portfolio trades. Before considering live capital, require complete quote/fill reconciliation, positive net return after all observed costs, positive contribution from both sleeves, no unresolved operational violations, and realized slippage compatible with the frozen one-second/five-basis-point stress. Passing those gates is evidence for a small controlled next step, not proof of a money printer.

The frozen historical capacity reference is a $2,000 normalized account base. At 5% entry displayed participation and the 1,000 ms/5 bp-per-side stress, it earned +42.31% additive over the full discovery history but only +0.20% in the latest twelve months. Average target-sleeve utilization was 64.9%. Larger-account percentage results are capacity constrained and must not be extrapolated from the ideal fixed-sleeve path.

As an adverse bound, charging another 25 bp at every historically depth-unsupported exit retained +33.79% additive return, +3.68%, +3.09%, and +27.03% chronology blocks, and 6.25% drawdown. At another 50 bp the first block became negative. This sensitivity is not a fill forecast; it defines why actual paper exit fills are the central remaining gate.
