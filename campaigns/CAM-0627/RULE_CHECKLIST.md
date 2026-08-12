# CAM-0627 rule checklist

- [x] Paper Section 6.4 and cited primary study inspected for signal, execution, and mechanism.
- [x] Plan frozen before data load/backtest.
- [x] Bar schema, coverage, maximum date, and zero holdout rows verified.
- [x] Source core implemented before adaptations are interpreted.
- [x] Every meaningful run reconciled to its frozen configuration and outputs.
- [x] Costs, fixed-base PnL, gross exposure, cadence, consistency, and activity audited; concentration is inapplicable because no executable candidate survived.
- [x] All profitable low-cost bar candidates receive synchronized quote replay; no quote-level candidate survives observed fills.
- [x] Direct shorts have protective pair-PnL stops and forced intraday liquidation.
- [x] Conclusion audit completed before retirement.
