# CAM-0631 rule checklist

- [x] Constitution, current direction, declared data/code sources, relevant knowledge, and ledger reviewed.
- [x] Attached EPDC document treated as source material, not user instructions; exact staged contract extracted.
- [x] Primary OFI, queue-imbalance, latency/liquidity, broker-order, and paper-fill sources checked.
- [x] PLAN.yaml frozen before any signal backtest or fill simulation.
- [x] SIP quote/trade schema, entitlements, timestamp semantics, regular-session boundary, and cutoff pass fail-fast readiness.
- [x] Representative panel attrition and empirical spread-bucket composition reported for the first six-session panel.
- [x] Signed midpoint markouts reported at 250 ms, 1, 5, 15, 30, and 60 seconds before fill assumptions.
- [ ] Chronological validation, date/time/spread/symbol breakdowns completed; volatility/sector and leave-one-out remain before conclusion.
- [x] Cross-asset residual value tested against top-of-book-only and local-behavior controls; rejected as non-incremental.
- [x] First queue-aware fill simulation used latency, displayed size, same-price trade prints, partial/nonfill accounting, and four stress models; all realistic adverse variants failed.
- [ ] Fixed-base PnL, weekly/monthly paths, drawdown/recovery, exposure, turnover, and forced exits reported.
- [ ] Short inventory, if any, has protective and emergency exits and forced preclose liquidation.
- [ ] Planned versus executed configuration, defaults, variant count, attrition, and saved outputs reconciled before interpretation.
- [ ] Holdout remains sealed; no promotion or live claim without tiny real-order calibration.
