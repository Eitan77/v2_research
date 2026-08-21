# CAM-0637 review

QQQ was mechanically much cleaner than SOXL: the average spread was 0.36 bp, while the cent-rounded target distances were 1.13 and 2.12 bp. Complete replay used 13,922,840 SIP quotes across all 159 required May 2025 windows.

It still did not produce an edge. The two-bp rule hit its target before its symmetric stop on 47.23% of 235 positions versus 52.77% stops. It lost 0.33% additive even with no stop slippage and 1.57% with one bp. The one-bp rule was worse at 43.40% targets and 56.60% stops. Holding-window changes did not matter materially because almost every OCO resolved within the first minute.

Thus QQQ solves the price-tick and spread objection but falsifies the chosen green-candle/high-volume continuation signal. No further threshold mining or profitability claim is justified.

