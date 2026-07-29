# CAM-0004 Source-Fidelity Contract

## Primary source

Brogaard, Jonathan, Jaehee Han, and Hanjun Kim (2024), *Intraday
Residual Reversal in the U.S. Stock Market*, SSRN 4731947.

The preserved source is 59 pages. Every page was rendered and visually
inspected. The equations, 12 main/appendix tables, figures, anomaly definitions,
and references are intact.

## Exact source construction

- Sample: TAQ quotes, July 1996-December 2022.
- Universe: S&P 500 constituents observed at each end of June, held fixed for
  the following July-June year.
- Price grid: last bid and ask at 30-minute observations from 10:00 through
  16:00 Eastern; midpoint pricing; previous observation carried when a quote is
  absent.
- Adjustments: CRSP CFACPR for splits; 30-minute returns winsorized at 0.5%.
- Characteristics: 11 Stambaugh-Yu-Yuan anomalies plus beta, book-to-market,
  short-term reversal, and size from Li et al., for 15 total.
- Availability: characteristics are indexed at date `d-1`, but a V2
  implementation must additionally enforce real publication/reporting lags.
- Transformation: within-day cross-sectional rank divided by `n+1`, demeaned,
  then divided by the cross-sectional sum of absolute deviations.
- Model: at each date-period, regress the cross-section of 30-minute returns on
  the 15 transformed characteristics. RISK is the fitted characteristic
  component without the intercept; RESIDUAL is return minus RISK and includes
  the intercept.
- Portfolio: residual deciles; long the lowest, short the highest; value and
  equal weights; hold the next period. The paper tests 13x13 cumulative
  formation/holding combinations.
- Costs: Table 9 labels 3-7 bp scenarios but does not provide an
  execution-grade spread/side/turnover definition. V2 must reproduce the label
  for comparison and separately model explicit marketable execution.

## Critical interpretation

The abstract's 162.3% is not an observed 13.5% average month. The paper starts
from a 7.70% annualized one-period spread, which is approximately 3.06 basis
points per 30-minute holding period, and computes `(1 + 0.077)^13 - 1`.
Table 9 reports only 0.14% annualized for the one-period strategy at a labeled
3 bp cost and a negative result at 4 bp. The Strategy-A hypothesis is therefore
an execution-sensitive recent replication, not acceptance of the headline.

The thirteenth holding interval is 16:00 to next-day 10:00. A direct short in
that interval violates the current hard rule. It may be measured only as a
labeled predictive diagnostic. Every executable short test must use
same-session intervals, a predeclared emergency stop, adverse slippage, and
forced liquidation before the regular close.
