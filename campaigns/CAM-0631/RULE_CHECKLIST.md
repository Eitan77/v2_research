# CAM-0631 rule checklist

- [x] Constitution, current direction, declared data/code sources, relevant knowledge, and ledger reviewed.
- [x] Attached EPDC document treated as source material, not user instructions; exact staged contract extracted.
- [x] Primary OFI, queue-imbalance, latency/liquidity, broker-order, and paper-fill sources checked.
- [x] PLAN.yaml frozen before any signal backtest or fill simulation.
- [x] SIP quote/trade schema, entitlements, timestamp semantics, regular-session boundary, and cutoff pass fail-fast readiness.
- [x] Representative panel attrition and empirical spread-bucket composition reported for the first six-session panel.
- [x] Signed midpoint markouts reported at 250 ms, 1, 5, 15, 30, and 60 seconds before fill assumptions.
- [x] Chronological confirmation, date/spread/symbol breadth, equal-symbol weighting, and peer-group residual attribution completed. Volatility/sector/leave-one-out portfolio attribution is inapplicable to the rejected sparse-window execution claim; no profitable portfolio exists to attribute.
- [x] Cross-asset residual value tested against top-of-book-only and local-behavior controls; rejected as non-incremental.
- [x] First queue-aware fill simulation used latency, displayed size, same-price trade prints, partial/nonfill accounting, and four stress models; all realistic adverse variants failed.
- [x] Fixed-base fill economics and forced-exit rates reported. Weekly/monthly portfolio paths, drawdown/recovery, exposure, and turnover are inapplicable because the source sampling covers four minutes on selected sessions and no executable configuration survived; no full-calendar PnL was invented.
- [x] Simulated short inventory was intraday only with a 15 bp protective stop, 60-second hard exit, and windows far before the close; historical borrow availability remains an explicit limitation.
- [x] Planned versus executed configuration, defaults, variant count, attrition, model hash, and saved outputs reconciled before every interpretation.
- [x] Holdout remained sealed. No promotion or live claim was made; late RUN-0007 validation was not opened after the development gate failed.
