# CAM-0007 Governing Checklist

## Launch and readiness

- [x] Re-read governing documents, Strategy A coverage, data/code declarations, relevant knowledge, and ledger.
- [x] Confirm earnings-conditioned information diffusion is materially distinct from CAM-0001 through CAM-0006.
- [x] Freeze PLAN before campaign-scoped readiness, targeted network pulls, or performance tests.
- [x] Validate local earnings timestamps, timezone/session mapping, duplicates, missing fields, and coverage boundary.
- [x] Validate a conservative news detector on the local event overlap before using it to fill missing dates.
- [x] Pull only point-in-time QQQ missing earnings-event metadata; no broad universe or sealed date.
- [x] Verify point-in-time membership, adjusted/raw price lineage, shifted liquidity, minute/daily paths, and multiday exits.
- [x] Record every source of event/sample attrition, artifact hashes, maximum date, and zero holdout.
- [x] Pass fixtures for event mapping, detector, timing, allocation, costs, stops, forced close, multiday exit, and drawdown.

## Investigation

- [x] Reproduce the frozen earnings-reaction baseline before adaptation.
- [x] Separate event alpha, price reaction, execution, and portfolio construction.
- [x] Test announcement timing, gap sign/magnitude, first-30-minute confirmation, volume, volatility, and market state causally.
- [x] Test 10:00-10:05 entry latency and same-day/1/3/5/10-session exits.
- [x] Test long and protected intraday-short controls with full-path safeguards.
- [x] Test costs, sizing, event overlap, neighbors, chronological folds, months, quarters, symbols, events, and concentration.
- [x] Apply targeted SIP NBBO/trade replay to every close or strong candidate. (Inapplicable: all bar-stage profiles remain far below Type A after 20 bp and 10:05 latency; replay cannot rescue missing alpha.)
- [x] Define activation, decay, suspension, and retirement logic for survivors. (No survivor; the bar-stage return and bootstrap stopping rule retires the campaign.)

## Conclusion

- [x] Complete the eleven-principle and three-perspective audit.
- [x] Preserve all failed runs and the sealed holdout.
- [x] Update results, review, worklog, ledger, knowledge, and current direction.
- [x] Continue to another mechanism unless 2-3 genuine Type-A candidates exist. (CAM-0008 frozen.)
