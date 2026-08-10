# CAM-0613 review — SSRN 3.14 Support and resistance

## Outcome

`retired_mechanism_exhausted`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `sp500__prior_pivot__target_exit__long`, -50.60% fixed-base additive net.
- Selected executable adaptation: `sp500__pivot_target__long__atr0.5` at -21.83%; development-only post-2024 return -15.04%; expanding walk-forward parameter-selection return -28.64%.
- No executable long candidate cleared the 2 bps bar gate; quote replay was inapplicable.

## Judgment

The result is interpreted as development evidence only. The audit separated long-only implementable sleeves from overnight or no-stop short diagnostics, tested broad parameter neighborhoods, periods, costs, contributors, and causal universes, and did not select a full-sample winning ticker basket. `retired_mechanism_exhausted` is the strongest claim supported by the saved artifacts.

## Mandatory conclusion audit

- Source definition and implementation contract reconciled.
- Point-in-time universes, filing availability, sample attrition, and cutoff checks reconciled.
- Fixed-base additive accounting, no-margin gross cap, monthly/yearly path, drawdown, activity, costs, and concentration saved.
- Mechanism-consistent adaptations and development-only chronological checks completed.
- Every profitable execution-qualified best adaptation received SIP quote replay; direct-short signal-only variants were not called executable.
- Maximum loaded date is 2026-04-30 and holdout rows loaded are zero.
- Promotion remains false.

## 2026-08-10 deep-development checkpoint

Paper section 3.14, **Support and resistance**. Source contract: Prior-day C=(H+L+Cclose)/3, R=2C-L, S=2C-H; long if current price>C and liquidate at R; short if current price<C and liquidate at S.

No structured survivor cleared the mechanism, cost, and recent-consistency screen after the repair loop.

Status: `retired_mechanism_exhausted`.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.


## Split-repaired checkpoint (RUN-0020/RUN-0021/RUN-0023)

Prior strategy evidence is invalid because the inherited stock panel adjusted forward splits in the wrong direction. The repaired structured result is **no_structured_survivor**. No mechanism-consistent repair cleared the structured screen. This remains adapted development evidence; the May 2026 holdout was not accessed and promotion is blocked.

## Ranked-pivot execution extension (RUN-0022–RUN-0024)

The causal ranked bar candidate earns +121.93% at 2 bp with 5.96% drawdown and daily activity, but marketable 09:40 execution loses 17.11% before extra slippage. The earlier passive result is invalid because target touches could precede entries. In the corrected sequential replay, a bid touch earns +12.84%, but 1 bp of through-price evidence cuts return to +2.62%; adding 1 bp per side leaves +0.28% with 6/6 positive/negative months. Do not promote without prospective queue-aware fills.
