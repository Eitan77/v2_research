# Current Research Direction

> This file states the active objective and the user's current preferences.
>
> Unless a value is labeled **hard**, treat it as a reference target for judgment rather than an automatic pass/fail boundary.

## Active system objective

Use an adaptive campaign loop:

```text
knowledge and current objective
→ broad campaign PLAN.yaml
→ starting run
→ diagnose weaknesses
→ justified adaptive runs
→ meaningful checkpoint
→ RESULTS.yaml and REVIEW.md
→ ledger and reusable knowledge update
```

One strategy idea should be pursued as one campaign. Do not create a full plan, review, and ledger entry for every holding-period or parameter adjustment.

## Research comparison standard

All strategy research is account-size independent.

Normalize each strategy to a fixed capital base of `1.0`, keep position sizing tied to that original base, and do not reinvest prior profits.

The primary sortable result is:

```text
cumulative net simple P&L return on normalized strategy capital
```

Also report monthly and calendar-year simple returns, standard running-peak-relative drawdown, recovery time, utilization, exposure, turnover, costs, stability, and concentration.

Do not rank strategies primarily by CAGR, compounded ending equity, a hypothetical account balance, or gross return before costs.

## Active research objective

Choose a coherent first campaign based on the available data, reliable code, execution realism, and relevance to the strategy profiles below.

The campaign should actively pursue its hypothesis by testing and adapting reasonable implementations. Codex should diagnose weaknesses and try targeted improvements rather than stopping after the first weak version.

Within a campaign, Codex may adjust holding periods, entries, exits, thresholds, filters, sizing, stops, execution assumptions, and portfolio construction when each adjustment has a clear reason.

Start a new campaign only when the core hypothesis, mechanism, information set, or strategy identity changes materially.

## Hard current risk constraint — direct short selling

Until explicitly changed by the user:

- Do not hold direct short positions in stocks or ETFs overnight.
- Direct stock or ETF shorting is allowed only in strictly intraday strategies.
- Every permitted short strategy must define a protective stop-loss or emergency exit.
- Every permitted short position must have a forced liquidation cutoff before the regular-session close.
- Backtests must model stop and forced-exit slippage realistically.
- A strategy cannot be presented as executable when the data cannot credibly model these exits.
- Intraday pairs, statistical-arbitrage, relative-value, and market-neutral strategies may use direct short legs under these requirements.
- Market neutrality does not exempt a short leg from stop, emergency-exit, realistic-execution, or forced-close requirements.

This is a hard requirement.

## Strategy profiles

These are reference objectives, not isolated pass/fail gates.

### A. Recent Money Printer

**Purpose:** Find a recently effective strategy that produces unusually high and consistently positive monthly income.

Reference objectives:

- Average monthly net simple return: approximately **10% to 15%**
- Preferred standard maximum drawdown: **below 20%**
- Preferred full drawdown-recovery time: **less than approximately one month**
- Ideally no negative months in the main recent evaluation period
- Preferably no more than one negative month when the sample is long enough
- Monthly returns should be relatively consistent rather than alternating between severe losses and exceptional gains
- Holding periods may range from approximately **one minute to ten trading days**
- The strategy may scalp, trade intraday, hold for several days, or rebalance a portfolio daily or weekly
- The result should not be overly sparse, concentrated, execution-dependent, or adapted to one fortunate episode

A sequence such as `+12%, +11%, +14%, +9%, +13%` is preferable to `-20%, +40%, -10%, +45%`, even when the arithmetic average is similar.

The campaign should investigate weaknesses and attempt principled improvements in consistency, drawdown, recovery, cost, concentration, and execution.

Any direct stock or ETF short component must be strictly intraday. Long positions may be held for up to approximately ten trading days.

### B. High-Quality Overall Strategy

**Purpose:** Find a strong standalone strategy suitable for meaningful ongoing allocation across multiple market conditions.

Reference objectives:

- Average calendar-year net simple return: approximately **50%**
- Preferred standard maximum drawdown: **below 10%**
- Reasonably fast recovery for the strategy's horizon
- Returns distributed across multiple periods rather than dominated by one regime
- Attractive results after realistic and adverse costs
- Broad and credible evidence across trades, periods, and reasonable implementations

A somewhat lower return may be superior when consistency, capacity, execution quality, or diversification is substantially better.

This strategy may not depend on carrying direct short stock or ETF positions overnight.

### C. Institutional Low-Risk Quant Sleeve

**Purpose:** Find a stable, low-drawdown edge that can be carefully leveraged or combined with independent sleeves.

Reference objectives before leverage:

- Average calendar-year net simple return: approximately **10%**
- Preferred standard maximum drawdown: approximately **2% or less**
- Fast and controlled recovery
- Stable positive net expectancy after conservative costs
- Low market beta
- Low correlation with other candidate sleeves, especially during stress

The underlying unlevered result must remain visible. Leverage is portfolio engineering, not improved alpha.

Intraday market-neutral, pairs, statistical-arbitrage, and relative-value strategies may use direct short legs under the current short-selling rule.

### Relationship among profiles

| Profile | Primary value | Return objective | Preferred maximum drawdown |
|---|---|---:|---:|
| Recent Money Printer | Consistent exceptional recent income | 10%–15% monthly | Below 20% |
| High-Quality Overall Strategy | Strong standalone growth | About 50% annually | Below 10% |
| Institutional Low-Risk Quant Sleeve | Stability, scaling, and combination | About 10% annually | About 2% |

A campaign may discover that its strategy fits a different profile than originally expected.

## Current research interests

The agent may research broadly, including:

- Intraday and interday momentum, continuation, reversal, and recovery
- Opening gaps and session reactions
- Overnight versus regular-session information
- Cross-sectional and market-residual behavior
- Volume, liquidity, breadth, and dispersion
- Volatility scaling and risk overlays
- Event-driven effects
- Weekly portfolio strategies
- Market-neutral and low-correlation sleeves
- Recent structural market changes

## Working instructions

- Research outside ideas when they add a mechanism, implementation, or criticism.
- Reuse code when it is appropriate and verified for the intended use.
- Do not treat old pipeline stages as the only valid research method.
- Prefer targeted adaptation to blind parameter expansion.
- Broad discovery scans are allowed when the question genuinely requires them.
- Continue a campaign while each new run has a clear reason and expected lesson.
- Record every meaningful run compactly, but reserve full reviews for checkpoints.
- Keep the ledger at the campaign level.
- Do not add old conclusions to V2 memory without independently checking their underlying evidence.
