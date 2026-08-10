# SMA rank-parameter neighborhood and quote replay — RUN-0044

## Outcome

The 126-session formation / 21-session skip rank remains the best general default. A
prespecified 600-variant surface, chronological selection split, expanding annual
walk-forward selection, and 95-configuration full-history SIP quote replay did **not**
support replacing it across the SMA families.

Two static revisions did improve on every required comparison after being selected
using the earlier 60% segment only:

| Candidate | Rank | Quote +2 bp net | Baseline | Max DD | Baseline DD | Recent 12m | Baseline recent |
|---|---:|---:|---:|---:|---:|---:|---:|
| QQQ triple MA, top 3 | 126 / 5 | +266.02% | +258.23% | 27.28% | 33.40% | +98.91% | +90.44% |
| QQQ triple MA, top 10 | 42 / 0 | +177.91% | +164.59% | 30.36% | 33.14% | +75.63% | +67.86% |

The top-10 revision is the cleaner finding. It raised positive recent months from 7/12
to 8/12, reduced the recent worst month from -3.95% to -3.09%, retained +171.22% at
quote plus 10 bp per side, and left +102.62% after removing its five largest symbol
contributors. Eleven neighboring QQQ triple-MA top-10 cells also dominated 126/21
after quote replay, so the result is a region rather than one exact optimum.

The top-3 126/5 revision reduced drawdown substantially and raised recent return, but
its full-history worst month worsened from -18.85% to -21.70%. It is useful as a risk/
recency alternative, not an unambiguous replacement.

## Search contract

- Five families: QQQ single MA150 weekly, QQQ dual MA50/200 weekly, QQQ triple
  MA10/50/200 monthly, S&P dual MA50/200 weekly, and S&P triple MA10/50/200 monthly.
- Breadths: 1, 2, 3, and 10.
- Formation sessions: 42, 63, 84, 126, 189, and 252.
- Skip sessions: 0, 5, 10, 21, and 42.
- 600 bar-stage configurations, evaluated at 2 and 10 bp per side.
- Equal-weight long-only positions, maximum gross exposure 1.0, no broker margin.
- Causal point-in-time membership and trailing top-half dollar-volume eligibility.
- Common comparison begins after 294 sessions, the maximum formation plus skip.
- QQQ selection/validation boundary: 2024-01-19. S&P boundary: 2024-10-16.
- Training-only selection maximized the minimum training return in the adjacent
  formation/skip neighborhood. Validation never changed the selected cell.
- Annual expanding walk-forward paths required at least 252 prior common sessions.

All 30 parameter cells were profitable at both 2 and 10 bp bar costs, and positive in
the later chronological segment, for every family/breadth pair. The momentum rank is
therefore broadly viable; this was a search for a better expression, not a rescue of a
fragile baseline.

## What did not survive

Only 5/20 training-selected cells improved full quote return, only 4/20 improved the
recent year, and only the two QQQ triple-MA cells above simultaneously improved full
return, recent return, chronological validation, and drawdown.

Expanding annual walk-forward selection beat the matched 126/21 quote path in only
2/20 cases:

- S&P dual-MA top 10: +116.74% versus +116.31%, an economically negligible +0.44 pp.
- S&P triple-MA top 10: +111.56% versus +98.18% from 2024 onward, with 22.24% versus
  30.47% drawdown. This is useful causal evidence, but it covers only 28 months and the
  selector changed from 42/0 in 2024 to 252/0 in 2025-2026.

The large post-hoc maxima should not be treated as confirmed rules. Examples include:

- QQQ dual-MA top 1, 84/21: +496.46%, versus +376.18% for 126/21.
- QQQ triple-MA top 2, 84/5: +406.85%, 21.07% drawdown, and +121.23% recently.
- S&P triple-MA top 2, 84/0: +309.30%, 23.58% drawdown, and +210.96% recently.

Those cells survived quote replay and cost stress, but the later segment participated
in identifying them and the training-only/walk-forward selectors did not reliably find
them. They are research leads, not deployable parameter choices.

## Execution and integrity

- Exact target-change SIP NBBO replay used the 09:30 midpoint reference and the first
  marketable 09:40 ask/bid, plus 0/1/2/5/10 adverse bp per side.
- 13,569 unique roles per clock and 41,372 candidate-tagged fill rows were reconciled.
- All 95 quote configurations achieved 100% role coverage and remained profitable at
  quote plus 10 bp; the smallest 10-bp net return was +14.15%.
- Final-session corporate-action exits were documented for ALXN, XLNX, TWTR, and ATVI.
- Full quote histories are QQQ 2019-06-21 through 2026-04-30 and S&P 2021-05-03
  through 2026-04-30. No row on or after 2026-05-01 was loaded.

Quote replay validates target-change execution. Daily holding P&L still uses the
split/dividend-repaired daily open-to-next-open series; it is not tick-by-tick marking
of every overnight holding interval.

## Decision

1. Keep QQQ dual-MA top 3 at 126/21 as the balanced general SMA candidate.
2. Add QQQ triple-MA top 10 at 42/0 as the strongest chronologically selected parameter
   improvement and QQQ triple top 3 at 126/5 as a secondary lower-drawdown alternative.
3. Preserve S&P triple top-10 walk-forward selection as a limited-history diagnostic.
4. Do not promote the isolated top-1/top-2 maxima or use the sealed holdout. A future
   confirmation must freeze the rule before observing new data.
