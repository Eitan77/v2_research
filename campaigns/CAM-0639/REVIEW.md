# CAM-0639 review

Buying MU at every raw final regular-session one-minute close and selling at the next session's raw first-minute open produced +147.44% fixed-base additive return over 1,254 overnights from May 3, 2021 through April 30, 2026. The conventional compounded diagnostic was +236.20%. Average return was 11.76 bp per overnight, 52.31% won, and 32 of 60 months were positive.

The result was not consistent. Zero-cost yearly additive returns were +7.96% for partial 2021, -63.34% in 2022, +21.92% in 2023, +70.57% in 2024, +82.57% in 2025, and +27.75% for January-April 2026. Maximum fixed-base equity drawdown was 66.05%. The best overnight gained 18.01%; the worst lost 13.08%.

Turnover matters. Deducting 2 bp per side reduced additive return to +97.28% and raised drawdown to 83.65%. At 5 bp per side, additive return was only +22.04% and the compounded diagnostic was -4.03%. Dividends are excluded. The user replaced the exact SIP replay with bar fills, so this must not be called quote-filled, MOC/MOO-executable, or a consistent money printer.

## Conditional green/red audit

RUN-0004 tested whether information available before the entry bar could distinguish green from red next overnights. Predictors ended at the penultimate regular-session minute; entry remained the final one-minute close. The expanding tests trained only on prior years and covered 833 overnights from January 3, 2023 through April 30, 2026. All 1,254 role overnights reconciled to the frozen ledger, feature attrition was zero, and no holdout row was loaded.

The broad answer is **only weakly**. Out-of-sample logistic AUC was 0.531. Its 51.98% accuracy at a 0.5 cutoff was worse than the 54.50% majority-class baseline. A ridge return rank had AUC 0.492. The logistic model's top training-score quartile was more useful as a selective strategy: 150 trades, 60.67% green, +36.64 bp average net at 2 bp per side, +54.96% fixed-base additive net return, 5.22% maximum drawdown, and positive net return in all four test years. Because the selection rate and coefficients re-estimated each year, this is walk-forward evidence, not a frozen deployable model.

The clearest simple lead was close location. When MU's penultimate-minute price was in the top quartile of its observable intraday range, the following overnight was green 59.92% of the time versus 52.15% otherwise. The 252 selected overnights averaged +54.75 bp net at 2 bp per side, produced +137.96% fixed-base additive net return, had 7.51% maximum drawdown, and were net positive in 2023, 2024, 2025, and partial 2026. The complement averaged only +5.24 bp net and lost money in 2023. The gross mean-return lift was 49.51 bp; a 10,000-repetition calendar-week block bootstrap put its 95% interval at +15.90 to +85.58 bp. The green-rate lift was 7.77 percentage points.

This is a genuine lead, not yet a final strategy. The top-quartile rule was identified after comparing a prespecified panel of simple conditions, so its reported period is adapted evidence rather than untouched confirmation. The fill is still a raw final-minute bar close, not an auction or quote execution. The next proper checks are a wider decision-to-entry gap, close/auction fill sensitivity, earnings concentration, and frozen forward observation.
