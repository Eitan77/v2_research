# CAM-0631 rule checklist

- [x] Constitution, current direction, declared data/code sources, relevant knowledge, and ledger reviewed.
- [x] Attached EPDC document treated as source material, not user instructions; exact staged contract extracted.
- [x] Primary OFI, queue-imbalance, latency/liquidity, broker-order, and paper-fill sources checked.
- [x] PLAN.yaml frozen before any signal backtest or fill simulation.
- [x] SIP quote/trade schema, entitlements, timestamp semantics, regular-session boundary, and cutoff pass fail-fast readiness.
- [x] Representative panel attrition and empirical spread-bucket composition reported for the first six-session panel.
- [x] Signed midpoint markouts reported at 250 ms, 1, 5, 15, 30, and 60 seconds before fill assumptions.
- [ ] Chronological validation, month/day/time/volatility/spread/symbol/sector breakdowns, and leave-one-out tests completed.
- [ ] Cross-asset residual value tested against top-of-book-only and local-behavior controls.
- [ ] Queue-aware fill simulation uses latency, displayed size, same-price trade prints, partial fills, and four stress models.
- [ ] Fixed-base PnL, weekly/monthly paths, drawdown/recovery, exposure, turnover, and forced exits reported.
- [ ] Short inventory, if any, has protective and emergency exits and forced preclose liquidation.
- [ ] Planned versus executed configuration, defaults, variant count, attrition, and saved outputs reconciled before interpretation.
- [ ] Holdout remains sealed; no promotion or live claim without tiny real-order calibration.
