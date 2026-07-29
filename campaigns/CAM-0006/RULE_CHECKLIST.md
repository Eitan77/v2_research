# CAM-0006 Governing Checklist

## Launch and readiness

- [x] Re-read governing documents, current Strategy A coverage, data/code declarations, relevant knowledge, and ledger.
- [x] Confirm official opening-auction inventory transfer is materially distinct from CAM-0001 through CAM-0005.
- [x] Freeze PLAN before campaign-scoped readiness or backtesting.
- [x] Decode and verify Q official opens against matching O trades; select largest matched NASDAQ opening size and reject 239 missing/unmatched plus ambiguous conflicts.
- [x] Verify point-in-time QQQ membership, raw daily lineage, SIP minute paths, prior-close mapping, common split rejection, valid sessions, and early-close handling.
- [x] Record attrition: 46,563 auction/member rows to 46,322 valid-session official opens to 44,549 causal signal-complete events across 116 symbols/460 sessions; 41,488 have all exact exit marks and 23,608 have a trade every minute. Future density never changes causal ranks. Hash every artifact; max date 2026-04-30; zero holdout.
- [x] Pass six fixtures for auction selection, ambiguity rejection, split detection, allocation, costs, stopped shorts, forced-close-compatible accounting, and drawdown.

## Investigation

- [x] Reproduce frozen continuation and absorption baselines before adaptation.
- [x] Separate auction alpha, first-minute confirmation, execution, and portfolio construction.
- [x] Test gap, auction-size, first-minute, liquidity, volatility, and market-state questions causally.
- [x] Test 09:31-09:35 entry latency and 09:45/10:00/12:00/15:55 exits.
- [x] Test long, protected-short, and balanced legs with full-path safeguards.
- [x] Test costs, sizing, breadth, neighbors, chronological folds, months, symbols, events, and concentration.
- [x] Apply targeted SIP NBBO/trade replay to every close or strong candidate.
- [x] Define activation, decay, suspension, and retirement logic for survivors.

## Conclusion

- [x] Complete the eleven-principle and three-perspective audit.
- [x] Preserve all failed runs and the sealed holdout.
- [x] Update results, review, worklog, ledger, knowledge, and current direction.
- [x] Continue to another mechanism unless 2-3 genuine Type-A candidates exist.
