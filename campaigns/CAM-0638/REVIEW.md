# CAM-0638 review

The conditional search did not find a credible QQQ microscalp. It evaluated 32 quote-executed target/stop/timeout configurations using 1/2/3/5-bp targets and stops, 1/3-minute limits, and one bp of adverse stop/timeout slippage. Triggers used only completed information: candle magnitude, causal relative volume, prior 3/5-minute returns, range, close location, time of day, and entry spread.

Selection used May 1-15 only. Ten simple candidates and three regularized multivariate candidates were locked and then evaluated on May 16-30; all 13 lost. The missing planned shallow interaction was reconciled separately: 2,848 development-eligible two-condition rows were measured, the top five were locked, and all five lost in validation. The closest simple validation result—green candle at least 10 bp with a 2-bp target and 5-bp stop—lost 0.154% over 35 trades. The closest depth-two result lost 0.142% over 28 trades.

This does not disprove every possible scalping mechanism. It rejects candle/volume/momentum conditioning of this market-buy QQQ micro-OCO family. A materially different attempt would need information closer to the matching engine—causal quote-size imbalance, signed trade flow, venue queue/rebates, or a discrete event catalyst—and independent data. It cannot be rescued honestly by selecting a later-half winner.

