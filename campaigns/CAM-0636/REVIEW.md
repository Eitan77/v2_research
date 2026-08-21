# CAM-0636 review

The literal May 2025 feasibility test says the order is possible but the strategy is not profitable. After a completed SOXL green candle of at least 20 bp with at least 2x causal same-clock volume, the replay bought the first next-minute SIP ask and immediately rested a sell limit labeled 1 or 2 bp higher. Because SOXL has a one-cent tick, both labels rounded to the same price and averaged a 5.93 bp effective target.

Across 1,296,147 SIP quotes with complete coverage, the subsequent bid reached the limit on 59.3% of positions within one minute, 76.9% within three minutes, and 78.7% within five minutes. Thus the mechanical answer is yes. The economic answer is no: forced exits averaged -34.1, -52.5, and -66.8 bp, producing -8.41%, -5.87%, and -7.16% additive PnL. Counting only completed targets would discard the inventory losses and manufacture profitability.

No queue assumption was needed for credited targets because a bid at or above the sell limit makes the order marketable. The result is still development evidence from one selected month and signal, but it is sufficiently negative that further threshold mining is not justified.

The symmetric OCO repair did not work. At the literal cent-rounded distance, the 5.93-bp stop equaled the average 5.93-bp spread: 91.4% of positions stopped in a median 25.8 milliseconds, only 8.6% hit the target, and net PnL was -6.11% with one bp of stop slippage. A prespecified spread-diagnosis tested 5/10/15/20/30-bp requested symmetric distances over 1/3/5-minute windows. None of 15 cells was profitable. Even at an effective 23.7-bp distance, the best target/stop rates were 33.8%/65.0%, far from the greater-than-half target rate a symmetric system needs after slippage.

This rejects the market-buy, immediate symmetric-OCO family for the tested signal and month. Entering passively at the bid could avoid paying the spread, but it changes the strategy into queue-dependent liquidity provision and cannot inherit these fill rates.
