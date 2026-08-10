# CAM-0606 review — SSRN 3.8 Pairs trading

## Outcome

`retired_mechanism_exhausted`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `etf__corr126__dislocation5__pair_dollar_neutral`, -17.79% fixed-base additive net.
- Selected executable adaptation: `nan` at +nan%; development-only post-2024 return +3.74%; expanding walk-forward parameter-selection return +nan%.
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

Paper section 3.8, **Pairs trading**. Source contract: Choose historically highly correlated pair; demean its completed simple or log returns; short positive demeaned rich leg and buy negative demeaned cheap leg; dollar neutral and gross normalized.

No structured survivor cleared the mechanism, cost, and recent-consistency screen after the repair loop.

Status: `retired_mechanism_exhausted`.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.


## Split-repaired checkpoint (RUN-0020/RUN-0021/RUN-0023)

Prior strategy evidence is invalid because the inherited stock panel adjusted forward splits in the wrong direction. The repaired structured result is **no_structured_survivor**. No mechanism-consistent repair cleared the structured screen. This remains adapted development evidence; the May 2026 holdout was not accessed and promotion is blocked.

## True-pair and execution extension (RUN-0022–RUN-0024)

The paper-identity long-cheap/short-rich ETF implementation closes the prior identity gap. Its best bar candidate, SMH/XLK, earns +21.14% at 2 bp with 3.32% drawdown but fails at 10 bp. Exact 09:40 SIP replay earns +1.58% at 2 bp with only 4/12 positive months; a causal first-10-minute convergence confirmation earns +1.69% at 2 bp and fails at 5 bp. The true pair is execution-sensitive and not a money printer.
