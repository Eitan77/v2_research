# Source notes

Kakushadze and Serur, Section 6.5, define risky-asset weight as target
volatility divided by forecast risky-asset volatility, with the remainder in a
risk-free asset. They suggest periodic or threshold-based rebalancing and allow
a leverage cap. This campaign fixes the cap at 1.0.

The source is not intraday. CAM-0628 is explicitly an adaptation: the weight is
computed at the prior close, applied only from the next open to the same-day
close, and the remainder stays in non-interest-bearing cash intraday. This tests
whether the source risk-budgeting principle can improve regular-session drift;
it is not described as a faithful replication of the weekly/monthly source.
