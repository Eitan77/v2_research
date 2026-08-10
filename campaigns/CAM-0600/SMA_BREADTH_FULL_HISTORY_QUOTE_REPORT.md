# SMA breadth experiment - full-history quote replay

## Contract

The five concentrated SMA families were frozen at breadths 1, 2, 3, and 10. The only changed variable is the number of equal-weighted stocks selected from the same causal momentum ranking. All 20 configurations received full-history target-change SIP quote replay using the 09:30 midpoint reference, marketable 09:40 NBBO execution, and 0/1/2/5/10 additional adverse bp per side. Accounting is fixed-base additive, gross exposure never exceeds 1.0, broker margin is prohibited, and no data on or after 2026-05-01 was loaded.

Coverage is 100% across 7,778 candidate-tagged fills. Six rows required terminal corporate-action handling: XLNX, TWTR, and ATVI were filled at their last valid SIP bid before their final regular-session close and referenced to that session's 09:30 midpoint. Scheduled and actual timestamps are retained.

## Quote plus 2 bp results

| Family | Breadth | Full net | DD | Recent 12m | Recent + months | Recent worst | Top-five share | Leave top five |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| QQQ single MA150 | 1 | +420.8% | 50.7% | +135.3% | 10/12 | -20.4% | 78.1% | +25.2% |
|  | 2 | +374.9% | 44.7% | +136.7% | 8/12 | -11.0% | 60.5% | +79.0% |
|  | 3 | +344.7% | 40.8% | +116.8% | 9/12 | -11.9% | 56.5% | +94.1% |
|  | 10 | +170.5% | 34.2% | +88.8% | 7/12 | -4.6% | 34.3% | +86.1% |
| QQQ dual MA50/200 | 1 | +376.2% | 31.0% | +138.6% | 10/12 | -20.4% | 61.2% | +101.9% |
|  | 2 | +365.7% | 24.8% | +141.1% | 8/12 | -11.0% | 57.9% | +96.6% |
|  | **3** | **+362.8%** | **17.5%** | +117.1% | 9/12 | -11.9% | 53.2% | **+119.1%** |
|  | 10 | +221.3% | 18.4% | +85.6% | 7/12 | -4.6% | 34.8% | +123.9% |
| QQQ triple MA10/50/200 | 1 | +377.9% | 35.6% | +176.9% | 10/12 | -7.9% | 78.5% | -16.0% |
|  | 2 | +281.0% | 29.4% | +100.0% | 8/12 | -14.6% | 61.3% | +34.5% |
|  | 3 | +258.2% | 33.4% | +90.4% | 8/12 | -9.3% | 51.0% | +70.3% |
|  | 10 | +164.6% | 33.1% | +67.9% | 7/12 | -3.9% | 34.9% | +85.9% |
| S&P dual MA50/200 | **1** | **+448.2%** | 28.9% | **+240.5%** | 11/12 | -18.7% | 81.4% | +26.7% |
|  | 2 | +300.4% | 23.9% | +181.0% | 11/12 | -14.8% | 70.2% | +14.8% |
|  | 3 | +248.4% | 25.9% | +161.5% | 11/12 | -6.8% | 57.2% | +40.1% |
|  | 10 | +155.2% | 27.0% | +106.8% | 9/12 | -5.8% | 33.9% | +77.5% |
| S&P triple MA10/50/200 | 1 | +370.3% | 28.0% | +244.0% | 11/12 | -12.3% | 88.7% | -45.2% |
|  | 2 | +250.9% | 28.6% | +182.4% | 11/12 | -2.7% | 74.3% | -15.4% |
|  | **3** | +210.0% | 30.2% | +169.6% | 11/12 | **-1.9%** | 67.3% | +7.9% |
|  | 10 | +110.0% | 27.8% | +100.2% | 9/12 | -2.1% | 42.1% | +35.4% |

All 20 configurations remain profitable at quote plus 10 adverse bp per side; the weakest still returns +105.3% additive over its available history.

## Findings

- Breadth exposes a strong rank gradient: top one has the highest median return, while top ten has the lowest. This supports genuine cross-sectional rank information, but it also reveals severe concentration.
- **S&P dual top one is the headline winner** at +448.2% full history and +240.5% recently. It is not the balanced winner: maximum drawdown is 28.9%, the recent worst month is -18.7%, and five symbols generate 81.4% of positive P&L. SNDK and PLTR alone contribute +336 percentage points.
- **QQQ dual top three remains the best balanced candidate.** Moving to top one or two adds little full-history return relative to the added drawdown. Top three has 17.5% drawdown and retains +119.1% after removing its five best contributors.
- **S&P triple top one and top two fail the concentration test.** Both become negative after removing their best five symbols. Top three remains the cleanest version of this recent-regime trade, while top ten is the breadth sensitivity.
- Top ten consistently reduces top-five dependence and recent worst-month severity, but typically sacrifices roughly half the headline return. It is a useful robustness control, not the return-maximizing construction.
- QQQ single remains inferior to QQQ dual at comparable breadth because its drawdowns are materially larger.

## Decision

Retain three frozen expressions for prospective comparison:

1. **QQQ dual top three** as the balanced full-history leader.
2. **S&P dual top one** as an explicitly aggressive concentration benchmark, not a primary promotion candidate.
3. **S&P triple top three** as the recent-consistency tactical candidate, with top ten as its breadth control.

No candidate is promoted from development evidence, and the sealed holdout remains untouched.
