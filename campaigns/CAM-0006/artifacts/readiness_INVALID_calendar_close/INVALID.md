# Invalid readiness snapshot

This snapshot was invalidated before any backtest or performance
interpretation. The minute extraction used a fixed 09:30-15:55 clock filter
without joining the actual market calendar. On early-close sessions, it could
therefore include after-hours bars after the 13:00 close. It also retained two
weekend auction records that had no valid market session.

The snapshot is preserved for audit only. Clean readiness must inner-join the
declared calendar, reject non-session auction rows, set forced liquidation five
minutes before each actual close, and evaluate path completeness against the
calendar-specific expected minute count.
