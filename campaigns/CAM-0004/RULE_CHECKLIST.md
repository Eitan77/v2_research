# CAM-0004 Governing Checklist

## Launch and source fidelity

- [x] Re-read AGENTS, Constitution, Current, data/code declarations, relevant Knowledge, Ledger, and the campaign template.
- [x] Confirm CAM-0004 is materially distinct from CAM-0001 through CAM-0003.
- [x] Read and visually inspect all 59 source pages; preserve PDF, text, contact sheets, and hashes.
- [x] Recover the source universe, June membership convention, quote grid, split handling, winsorization, 15 anomalies, rank normalization, cross-sectional regression, residual definition, deciles, weights, formation/holding periods, long/short legs, and stated costs.
- [x] Record that 162.3% is a compounded source annualization, not observed monthly simple P&L.
- [x] Mark the source's 16:00-next-10:00 short as diagnostic-only under the no-overnight-short rule.
- [x] Freeze PLAN.yaml before market-data readiness or backtesting.
- [x] Record source provenance and the canonical plan hash; code/data provenance remains pending until readiness identifies the implementation and datasets.

## Data and implementation gates

- [x] Verify declared schemas and paths for cutoff-bounded minute bars/quotes, daily characteristics, corporate actions, and membership. Exact S&P 500 membership/characteristics are absent; point-in-time QQQ and Alpaca split-adjusted proxy inputs passed.
- [x] Verify every source characteristic's definition and causal requirement; exact fields are unavailable and RUN-0001 is blocked rather than silently proxied.
- [x] Measure parent inventory and date, symbol, row, period, and field attrition for readiness and RUN-0002.
- [x] Prove maximum loaded date <= 2026-04-30 and zero holdout rows before signal/backtest.
- [x] Verify XNYS regular 30-minute boundaries, available-at timing, split adjustment, following-bar mapping, and overnight exclusion for the adapted screen. Exact midpoint/missing-quote replication remains blocked with RUN-0001.
- [x] Pass fixtures for cutoff, cross-sectional rank/L1 normalization, intercept treatment, deciles, lagged beta shape, following-action costs, fixed-base drawdown, and protected short stop.
- [x] Freeze and reconcile RUN-0001 blocker and RUN-0002 command, defaults, 12 variants, and outputs.

## Runs and required investigation

- [!] Exact source diagnostic blocked before RUN-0001 by absent declared point-in-time S&P 500 June membership, exact 15 causal characteristics, and TAQ-equivalent midpoint data. It was not silently proxied and is excluded from the retirement claim.
- [x] Preserve every meaningful run's parent, change, reason, expected effect, configuration, factual result, decision, and next question.
- [x] Diagnose alpha, execution, and portfolio construction separately after every meaningful run.
- [x] Examine gross and net performance by period, month, chronological fold, leg, and market/liquidity state. Symbol/sector leave-one-out was inapplicable to promotion because no branch came close and top-day concentration already failed.
- [x] Separate long-low, protected short-high, and same-session long-short components. Overnight short diagnostics were inapplicable under the hard rule.
- [x] Investigate formation/holding horizons, breadth, weights, netting/staggering, costs, and following-action latency.
- [x] Investigate four explicitly labeled adapted residual controls: full proxy, beta-only, price/liquidity, and return-risk. Exact source and sector models remained input-blocked.
- [x] Investigate the declared point-in-time QQQ proxy universe, causal stress subsets, broad tails, and concentrated tails without future-winner selection. S&P 500 testing remained input-blocked.
- [x] Measure sample size, chronological halves, effective active days, top-day contribution, observable stress properties, and causal selectability. Symbol leave-one-out was not decision-relevant after the top-day and economic gates failed.
- [x] Apply the frozen emergency stop, adverse slippage, and same-session liquidation to every executable short screen.
- [x] SIP NBBO/trades marked inapplicable: no bar-stage candidate was close, strong, or execution-questionable, and quote replay cannot create the missing order of magnitude.
- [x] Define causal high-noise/high-volatility activation and retirement logic; no surviving candidate requires a decay monitor.

## Conclusion and continuity

- [x] Complete the eleven-principle audit and researcher/skeptic/portfolio-engineer review before promotion, retirement, or pivot.
- [x] Confirm no careful skeptical quant would identify an obvious available-data experiment capable of closing the roughly tenfold net monthly gap; exact-source work remains explicitly blocked.
- [x] Update RESULTS, REVIEW, WORKLOG, LEDGER, KNOWLEDGE, and CURRENT at checkpoints.
- [x] Preserve the sealed holdout and all failed runs.
- [x] Do not call Strategy A exhausted from one campaign; begin a materially different campaign after CAM-0004.
- [ ] Continue the broader objective until 2-3 genuine Type-A candidates survive all applicable evidence gates.
