# Source notes

Section 15.3 describes the distress-risk puzzle as buying the safest companies
and selling the riskiest, using modeled bankruptcy probability and typically
rebalancing monthly. Section 15.3.1 states that the healthy-minus-distressed
portfolio can have unstable beta around downturns and prescribes multiplying
exposure by target volatility divided by trailing realized HMD volatility. It
explicitly permits capping that multiplier at 1.0 instead of leveraging.

CAM-0629 retains the safest-company rank and capped volatility scaling but is an
intraday long-only adaptation: it buys the safe sleeve after the next open and
returns to cash before the close. It does not claim to replicate the omitted
distressed short leg or the source's multiweek holding period.
