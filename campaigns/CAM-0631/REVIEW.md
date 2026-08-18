# CAM-0631 checkpoint review after RUN-0002

EPDC has cleared the first necessary mechanism test, provisionally. It has not cleared the execution or profitability test.

The source-faithful sequence was preserved. RUN-0002 reconstructed SIP NBBO and trade state for 24 prespecified liquid stocks on six chronological pre-cutoff sessions, evaluated every valid quote update in fixed two-minute morning and midday windows, and measured side-signed future midpoint markout without assuming a fill. All 576 quote/trade jobs completed, no job had zero candidate events, the maximum loaded session was April 1, 2026, and no holdout data was requested or loaded.

The chronological logistic model was trained on the first four dates and evaluated only on November 5, 2025 and April 1, 2026. Across 62,484 validation candidates, its AUC was 0.569. The lowest predicted-toxicity fifth had pooled mean signed markouts of +0.18, +0.18, +0.48, +1.21, +1.37, and +1.86 bp at 0.25, 1, 5, 15, 30, and 60 seconds. The highest-toxicity fifth was +0.11, +0.04, -0.23, -0.85, -1.06, and -1.41 bp. This is economically and causally aligned with the EPDC hypothesis: selected state contains information about whether a passive-side midpoint move is favorable or toxic.

The pooled result is not robust enough to start fill simulation. Raw quote updates are highly dependent and uneven across stocks. On November 5, raw-event long-horizon bucket ordering reversed, although 100 ms clustering followed by equal symbol weighting restored a +0.52 bp low-minus-high difference at 5 seconds; the same diagnostic was +0.60 bp on April 1. The current feature vector also mixes buy and sell states without expressing every directional feature in candidate-side coordinates, and microprice edge is mechanically related to spread and imbalance. Those are model-specification issues, not permission to tune toward the favorable pooled chart.

Decision: continue the signal stage. The next run will correct side symmetry and dependence using the same frozen data, then a separate comparison will ask whether cross-asset residual information adds value beyond own-book/local state. Queue-aware passive fills remain blocked until the sign and bucket ordering survive both validation dates, symbol weighting, adjacent horizons, and reasonable feature ablations. There is no net P&L, executable, paper, or live claim at this checkpoint.

## RUN-0003 robustness update

The side-symmetric correction passed. After 100 ms clustering and equal symbol-session training weights, the base five-second model preserved favorable low-versus-high toxicity ordering on both validation dates. November's equal-symbol means were +0.39 versus +0.07 bp at five seconds and +0.67 versus -0.44 bp at fifteen seconds; April's were +0.70 versus -0.40 bp and +0.99 versus -0.40 bp. The favorable target-horizon ordering held across 66.7% and 70.8% of symbols with both buckets.

The mechanism diagnosis is useful. Removing local one/five-second returns left the ordering intact, while removing signed one-second trade flow broke the weaker November five-second result. The evidence currently points to quote/flow toxicity avoidance rather than a conventional short-term directional trend. The source-prescribed cross-asset residual comparison is now justified. Fill simulation remains blocked until that comparison is complete and the limited six-session sampling is expanded or otherwise stress-tested.

## RUN-0004 cross-asset update

The peer residual did not add stable information. The own-book/flow control produced five-second low/high equal-symbol markouts of +0.39/-0.04 bp in November and +0.73/-0.28 bp in April, with 83.3% and 75.0% symbol breadth. Adding local returns did not improve the joint profile. Adding train-only leave-target-out peer residuals raised November's low bucket to +0.47 bp, but slightly lowered AUC and breadth; in April it lowered AUC, low-bucket markout, and breadth relative to the simpler control. Only 75 of 51,580 clusters were removed by the peer join, so sample attrition does not explain the comparison.

Decision: reject the cross-asset layer as non-incremental. Freeze the simpler own-book/flow specification and test it unchanged on a wider set of untouched pre-cutoff dates. This is a deliberately adverse choice against complexity: the source's novel peer feature was plausible, but the evidence does not support paying for it. Queue-aware fill simulation is still premature until the date sample is broader.
