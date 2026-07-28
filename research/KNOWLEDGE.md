# Quant Pipeline Starting Knowledge Base

## Purpose

This document is the shared starting knowledge for the Quant Pipeline V2 researcher.

It is not intended to contain every indicator or every possible strategy. It should preserve reusable concepts, mechanisms, research methods, and general failure lessons needed to reason intelligently.

Add concise, reusable knowledge at meaningful campaign checkpoints. Keep run-specific details in the campaign's run files and `WORKLOG.jsonl`.

---

# 1. Fundamental research model

## 1.1 A strategy has several separable layers

A strategy should not be treated as one indivisible object.

### Mechanism

Why might the opportunity exist?

Examples:

- Delayed information incorporation
- Behavioral underreaction or overreaction
- Forced flows
- Liquidity provision
- Risk transfer
- Index or fund rebalancing
- Volatility targeting
- Dealer hedging
- Short-sale constraints
- Institutional execution patterns
- Tax, calendar, or reporting effects

### Signal

How is the mechanism measured using information available at the decision time?

### Trade expression

How does the signal become an entry, exit, long/short decision, or ranking?

### Execution

Can the desired trade be completed after spread, delay, slippage, liquidity, and fill uncertainty?

### Portfolio construction

How are multiple opportunities sized, diversified, capped, hedged, or combined?

A failed implementation does not always invalidate the mechanism. A statistically significant signal does not automatically create an executable strategy.

## 1.2 Capital normalization and noncompounded P&L

Research comparisons should be independent of actual account size.

Use a fixed normalized strategy-capital base of `1.0`.

Position sizes may vary under the declared strategy rules, but every position must be sized from the original base. Do not reinvest prior gains and do not reduce later sizing because of prior losses.

Primary return:

```text
net simple return
= cumulative net P&L / fixed normalized strategy capital
```

Useful period summaries:

```text
monthly net simple return
= net P&L earned during the month / fixed normalized strategy capital

calendar-year net simple return
= net P&L earned during the year / fixed normalized strategy capital
```

Useful capital measurements:

```text
average capital utilization
= average gross capital in use / fixed normalized strategy capital

gross notional turnover
= total traded notional / fixed normalized strategy capital

net P&L per unit of turnover
= cumulative net P&L / total traded notional
```

Do not divide P&L by cumulative traded notional and call that return on capital. Traded notional measures turnover, not the capital base.

Construct the research equity curve as:

```text
equity_t = 1.0 + cumulative net P&L_t
```

Calculate standard drawdown relative to the running equity peak:

```text
running_peak_t = max(equity_s for s <= t)

drawdown_t
= (running_peak_t - equity_t) / running_peak_t
```

Maximum drawdown is the largest value of `drawdown_t`.

The denominator is the running equity peak, not the original capital base. Fixed-base position sizing and peak-relative drawdown are compatible: position size does not compound, but accumulated profits still raise the equity peak against which subsequent losses are measured.

Example:

```text
equity: 10 → 100 → 90 → 110
maximum drawdown: 10%
```

Optionally report:

```text
fixed-base P&L giveback
= (peak cumulative P&L - current cumulative P&L)
  / original fixed capital
```

In the example above, the fixed-base giveback is 100% of the original capital, but the standard drawdown is 10%. Never label fixed-base giveback as maximum drawdown.

Actual account size, whole-share constraints, broker margin, and dollar capacity belong to deployment analysis, not core research ranking.

## 1.3 Alpha, risk, and leverage are different

Leverage scales both expected return and risk. It does not create alpha.

A low simple-return strategy can be attractive when it has:

- Stable positive net expectancy
- Low drawdown
- Low volatility
- Low tail risk
- Strong capacity
- Low correlation with other strategies
- Efficient capital use

A high simple-return strategy can be unattractive when its performance comes from:

- Hidden leverage
- Concentrated exposure
- Unrealistic fills
- One exceptional event
- Unstable parameters
- A favorable market beta

Evaluate the unlevered strategy first. Then test scaling as a separate portfolio decision while keeping the underlying fixed-capital result visible.

## 1.4 Predictive evidence and strategy evidence differ

A feature may predict a future return spread but still fail as a strategy because:

- The spread is smaller than costs
- Opportunities overlap
- Long and short legs cannot be executed symmetrically
- Capital is mostly idle
- The edge occurs in illiquid names
- The target is not a tradable price
- Portfolio construction dilutes or concentrates the edge

Use anomaly research to identify relationships. Use strategy and execution research to test economic usefulness.


## 1.5 Adaptive campaign research

Treat one coherent strategy idea as one research campaign.

A campaign may contain many runs that test or improve:

- Holding periods
- Entry and exit timing
- Broad thresholds
- Filters tied to a diagnosed weakness
- Position sizing
- Stops and risk overlays
- Long and short components
- Execution assumptions
- Universe choices consistent with the same mechanism
- Portfolio construction

These changes do not require a new campaign while the underlying source of edge remains the same.

For each run, save only:

- The exact configuration
- The parent run, if any
- The reason for the change
- The expected effect
- The factual result
- The resulting decision

A new campaign is needed when the core hypothesis, mechanism, information set, or strategy identity changes materially.

Do not require a full human-readable review after every run. Write a campaign review only at a meaningful checkpoint, such as:

- A strong candidate is identified
- The campaign is being materially redirected
- The strategy is ready for confirmation or forward testing
- The campaign is being stopped
- The campaign splits into distinct strategy ideas

This structure permits active adaptation without losing the research trail.


---

# 2. Main strategy mechanisms

## 2.1 Momentum and continuation

Momentum assumes that recent relative or absolute movement continues.

Possible mechanisms:

- Slow information diffusion
- Institutional order splitting
- Investor underreaction
- Trend-following flows
- Attention and liquidity effects
- Risk-management feedback

Common forms:

- Time-series momentum
- Cross-sectional momentum
- Opening continuation
- Intraday range-position continuation
- Residual momentum
- Earnings or event drift
- Volume-confirmed continuation

Important questions:

- What information caused the original move?
- Is the horizon long enough for continuation but short enough to avoid reversal?
- Is the effect stronger after abnormal volume or broad market confirmation?
- Is performance explained by market beta or sector exposure?
- Do costs erase short-horizon versions?
- Is recent strengthening broad or concentrated?

Momentum signals are often redundant. Price location, breakout state, VWAP slope, recent return, and bar close location may measure the same latent continuation factor.

## 2.2 Mean reversion and reversal

Mean reversion assumes that an unusual move partially reverses.

Possible mechanisms:

- Temporary liquidity imbalance
- Overreaction
- Market-maker inventory
- Forced trading
- Short-term price pressure
- Closing or opening auction effects

Common forms:

- Short-term return reversal
- Market-residual reversal
- Gap fade
- VWAP displacement
- Breadth reversal
- Post-shock recovery
- Cross-sectional loser rebound

Important questions:

- Is the move caused by temporary pressure or new information?
- Does the reversal begin immediately or after a delay?
- Is the strategy providing liquidity and therefore exposed to adverse selection?
- Do stops conflict with the recovery mechanism?
- Does the effect disappear during high-information events?
- Can passive execution be modeled realistically?

A mean-reversion strategy often experiences adverse movement before recovery. Tight stops can destroy the edge by forcing exits during normal signal behavior.

## 2.3 Breakouts

Breakouts assume that movement beyond a prior range signals new information, demand, or a change in equilibrium.

Useful conditioning may include:

- Volume
- Range compression before the breakout
- Market and sector alignment
- Close location
- Time of day
- Volatility regime
- Retest behavior

Breakout systems are vulnerable to false breaks, spread expansion, and crowded entries. Avoid optimizing precise breakout levels without a mechanism.

## 2.4 Gaps and overnight information

A gap reflects information or positioning between the prior close and the next open.

Possible outcomes:

- Continuation because the information is underreacted to
- Reversal because opening prices overshoot
- Conditional behavior based on first-hour confirmation
- Different behavior for market-wide and idiosyncratic gaps
- Different behavior after earnings, news, or no obvious event

Separate:

- Overnight return
- Opening auction price
- First-bar or first-hour response
- Market-relative gap
- Gap fill
- Subsequent regular-session return
- Multi-day drift

Execution near the open can be expensive and volatile. A good gross gap effect may require delayed entry, selective participation, or a longer holding period.

## 2.5 Volatility effects

Volatility can be:

- A predictor
- A regime variable
- A risk measure
- A sizing input
- An execution cost driver

Possible strategies include:

- Volatility expansion continuation
- Post-volatility-shock reversal
- Low-volatility cross-sectional effects
- Volatility-targeted trend following
- Dispersion strategies

Volatility scaling can reduce exposure during high-risk periods, but it can also reduce exposure precisely when expected returns are strongest. Test whether signal strength and volatility change together.

## 2.6 Volume and liquidity

Volume may indicate:

- Information arrival
- Institutional participation
- Attention
- Liquidity
- Exhaustion
- Forced flow

Relative volume is more interpretable than raw volume across symbols, but construction must be point-in-time and account for time-of-day seasonality.

High volume can confirm continuation or signal exhaustion depending on mechanism and horizon.

Dollar volume and spread determine capacity and cost. A statistical effect in illiquid securities may not be usable.

## 2.7 Market residual and relative-strength effects

A stock's return can be decomposed into:

- Broad market exposure
- Sector or industry exposure
- Idiosyncratic or residual movement

Residual signals may better isolate stock-specific information, but beta and factor estimates must use only prior data and should be stable enough for the horizon.

A market-residual reversal may reflect temporary idiosyncratic pressure. Residual momentum may reflect stock-specific information diffusion.

## 2.8 Breadth and dispersion

Breadth measures how widely market movement is shared.

Dispersion measures differences among individual asset returns.

Potential uses:

- Confirming broad momentum
- Identifying narrow index moves
- Detecting exhaustion
- Conditioning cross-sectional strategies
- Measuring regime changes
- Separating index concentration from broad participation

Breadth features should use a point-in-time eligible universe and avoid letting future membership affect historical values.

## 2.9 Event-driven effects

Events can create cleaner hypotheses than generic indicator scans.

Examples:

- Earnings
- Guidance
- Analyst revisions
- Index additions and deletions
- Corporate actions
- Options expiration
- Rebalances
- Macro releases
- Regulatory changes
- Large news shocks

Event research must distinguish announcement time, public availability, after-hours trading, and next actionable price.

Sparse event strategies can be meaningful with fewer trades, but evidence should be judged by independent events, breadth, and comparability rather than raw row count alone.

## 2.10 Seasonality

Possible seasonal effects include:

- Day of week
- Turn of month
- Month end
- Quarter end
- Holiday periods
- Options expiration
- Tax-related periods
- Intraday time-of-day behavior

Seasonality is easy to mine. Prefer mechanisms tied to recurring flows and test whether the effect is stable across many independent occurrences.

---

# 3. Horizons and research requirements

## 3.1 One-minute and short intraday strategies

These depend heavily on:

- Accurate timestamps
- Bid/ask data
- Spread
- Latency
- Queue position
- Partial fills
- Market impact
- Adverse selection
- Time-of-day liquidity
- Order type

Bar-only backtests are usually insufficient for strong execution claims.

High trade counts do not compensate for a biased fill model.

## 3.2 Multi-hour and interday strategies

These reduce some microstructure sensitivity but still require:

- Realistic next-actionable prices
- Overnight-gap treatment
- Corporate actions
- Delisting and membership handling
- Borrow and short feasibility
- Costs and turnover
- Overlapping-position accounting

These horizons are a practical starting point for the current V2 system because they permit mechanism-driven research without making queue modeling the central problem.

## 3.3 Weekly and portfolio strategies

These should emphasize:

- Cross-sectional construction
- Rebalance timing
- Factor exposure
- Diversification
- Turnover
- Sector and position caps
- Portfolio correlation
- Capacity
- Long historical coverage
- Regime performance

A weekly strategy may be useful at lower standalone returns if it diversifies higher-frequency sleeves.

---

# 4. Current direct-short-selling constraint

Until the user changes the rule, direct short sales of stock or ETF shares are allowed only in strictly intraday strategies.

Required controls:

- Protective stop-loss or emergency-exit logic defined before testing
- Forced full liquidation before the regular-session close
- Realistic modeling of trigger delay, spread, slippage, and available liquidity
- Explicit consideration of trading halts and stop execution worse than the trigger
- No intentional after-hours, overnight, or multi-day direct short exposure

The main purpose is to avoid asymmetric overnight gap risk, where a short position can lose more than the expected stop distance before the next actionable trade.

Intraday pairs-trading, statistical-arbitrage, relative-value, and market-neutral strategies may use direct stock or ETF shorts under these requirements. Market neutrality does not exempt any short leg from its protective stop, emergency-exit, realistic execution, and forced-close requirements.

A stop does not guarantee execution at the stop price.

If the data cannot model these controls credibly, the short side may be studied only as a predictive diagnostic and should not be described as an executable strategy.

This rule concerns direct short sales of stock and ETF shares. Defined-risk alternatives and long inverse products are separate implementations and require independent analysis.

---

# 5. How to interpret common result profiles


## 4.1 Low return, low drawdown

Do not automatically reject.

Investigate:

- Whether the net edge is statistically and economically positive
- Capital utilization
- Moderate volatility targeting
- Broader universe application
- Combination with orthogonal strategies
- Capacity
- Stability across periods
- Tail behavior

Do not apply leverage to rescue noise.

## 4.2 High return, high drawdown

Diagnose the drawdown before adding a stop.

Ask:

- Was the drawdown gradual or caused by gaps?
- Was it one symbol, one event, or correlated portfolio exposure?
- Did the signal remain predictive while sizing failed?
- Did volatility expand before the loss?
- Was leverage the primary cause?
- Could diversification or exposure caps help?

Stops are plausible when a measurable event invalidates the original thesis. They are less plausible when chosen only to remove the worst historical loss.

## 4.3 Strong gross return, weak net return

Possible responses:

- Longer holding period
- More selective signals
- Lower turnover
- Trade netting
- More liquid universe
- Different entry time
- Better execution model
- Avoiding low-liquidity periods
- Portfolio-level batching

Do not assume the alpha is useless until the cost source is understood.

## 4.4 Strong recent performance, weak long history

Possible interpretations:

- New structural phenomenon
- Temporary regime
- Chance concentration
- Data or implementation change
- Improved liquidity or market participation
- Options-related or index-related change

Require:

- Multiple recent subperiods
- Breadth
- Mechanism
- Forward tracking
- Decay monitoring
- Honest recent-regime labeling

## 4.5 Strong historical average, recent weakness

Possible interpretations:

- Effect decay
- Crowding
- Structural market change
- Temporary adverse regime
- Execution deterioration
- Data issue

Do not preserve a historical strategy solely because it once worked. Determine whether the mechanism and current evidence remain active.

## 4.6 One-month or one-symbol concentration

Concentration is not automatically invalid for every event strategy, but it changes the claim.

Investigate:

- Performance excluding the top contributor
- Similar events or symbols
- Whether the contributor was predictable ex ante
- Whether the mechanism should generalize
- Whether the strategy is actually a disguised bet on one exposure

Do not exclude the concentrated loser or winner and then call the revised result independent.

## 4.7 Broad parameter stability

A credible mechanism often works across a neighborhood of reasonable implementations.

This does not mean every parameter must be profitable. It means the conclusion should not depend on a single precise value discovered after a large search.

Broad thresholds and a small number of interpretable alternatives are generally more convincing than dense optimization.

---


# 6. Strategy comparison and ranking

## 5.1 Primary ranking field

The primary sortable performance measure is:

```text
cumulative net simple P&L return on normalized strategy capital
```

Do not rank strategies by CAGR, compounded ending equity, or a real account balance.

Show the primary return alongside:

- Standard running-peak-relative maximum drawdown
- Average monthly net simple return
- Average calendar-year net simple return
- Calendar-year return history
- Average capital utilization
- Maximum gross exposure
- Turnover and cost sensitivity
- Trade or independent-event count
- Stability and concentration

Do not collapse these into an opaque universal score. The researcher should interpret why one strategy is preferable for its intended use.

## 5.2 Sparse versus continuously invested strategies

A sparse strategy may have low full-period simple return but high efficiency while active.

A continuously invested strategy may have higher total simple return but consume capital most of the time.

Report both:

- Return on the fixed strategy-capital base
- Capital utilization
- P&L per unit of turnover
- Time in market

This allows the AI to recognize a useful sparse sleeve without pretending that idle capital automatically earned the same return.

## 5.3 Risk-scaled variants

A leveraged or volatility-targeted version is a portfolio expression of an underlying strategy.

Keep the unscaled fixed-capital result visible and report the scaled variant separately.

Do not describe higher return caused only by added leverage as improved alpha.

---

# 7. Risk and portfolio construction

## 6.1 Volatility targeting

Volatility targeting adjusts position size based on estimated risk.

Potential benefits:

- More stable portfolio risk
- Lower exposure during volatility spikes
- Better comparison across strategies

Potential weaknesses:

- Estimation lag
- Forced deleveraging after volatility rises
- Reduced exposure during high-return periods
- Increased turnover

Test volatility estimation windows broadly. Do not select one precise window solely because it maximizes historical net simple return.

## 6.2 Position and exposure limits

Limits can reduce damage from:

- One symbol
- One sector
- Correlated positions
- Market beta
- Illiquidity

But limits may also dilute a genuinely concentrated event strategy. Explain what risk the cap addresses.

## 6.3 Stops and exits

Possible exits include:

- Fixed price stop
- ATR or volatility stop
- Time stop
- Signal invalidation
- Trailing stop
- Profit target
- Reversal signal
- Scheduled rebalance

The exit should match the mechanism.

For continuation, a reversal or failure-to-progress rule may be meaningful.

For mean reversion, a tight price stop may systematically exit before recovery.

For overnight gaps, simulated stop prices may be unavailable.

## 6.4 Combining strategies

Portfolio value depends on:

- Expected net return
- Drawdown
- Tail correlation
- Regime correlation
- Capital overlap
- Turnover netting
- Capacity
- Execution timing

A modest simple-return strategy may improve the total portfolio if it performs during other strategies' drawdowns.

Do not rely only on full-sample correlation. Examine stressed periods and drawdown overlap.

---

# 8. Execution knowledge

## 7.1 Market orders

Marketable execution usually pays the spread and may experience slippage.

Model:

- Decision delay
- Bid/ask side
- Available size
- Volatility
- Time of day
- Order size
- Market impact when relevant

## 7.2 Passive orders

A quote reaching or crossing a limit price does not prove a fill.

Important factors include:

- Queue position
- Displayed size ahead
- Actual trade prints
- Partial fills
- Order cancellation
- Quote flicker
- Hidden liquidity
- Adverse selection

Treat simple quote-touch simulation as an optimistic bound unless calibrated.

## 7.3 Closing and opening prices

Official open and close prices may come from auctions.

A strategy cannot assume it decides using the final auction price and also fills at that same price unless the order was submitted before the relevant cutoff using available information.

Use the next truly actionable price.

## 7.4 Transaction-cost stress

A strategy should be examined under:

- Base realistic costs
- Moderately adverse costs
- Severe but plausible stress

Cost sensitivity is itself useful evidence.

A strategy that changes from excellent to deeply negative after a few basis points may have little economic margin.

---

# 9. Statistical and validation knowledge

## 8.1 Multiple testing

Testing many features, horizons, thresholds, and variants increases the chance of false discoveries.

Track the effective research breadth, not only the final displayed strategy.

Formal FDR control is useful for broad anomaly scans, but strategy-level adaptation also creates selection risk that may not be captured by a single p-value adjustment.

## 8.2 Walk-forward testing

Chronological folds help measure stability and changing regimes.

A fold is not genuinely untouched if its result was repeatedly used to redesign the strategy.

Use folds for diagnosis, but reserve independent confirmation and forward data for final claims.

## 8.3 Holdout decay

A holdout loses evidentiary value when many strategies are evaluated against it and the results influence future development.

Use the sealed holdout rarely and deliberately.

Forward paper data is especially valuable for recent and heavily revised strategies.

## 8.4 Statistical significance and economic significance

A tiny effect can be statistically precise but economically unusable after costs.

A large effect can be statistically uncertain because of few events.

Interpret both dimensions.

## 8.5 Trade count and independence

Rows are not always independent trades.

Overlapping positions, repeated observations from the same event, correlated symbols, and clustered market days reduce effective sample size.

For sparse strategies, count independent events and regimes, not only orders.

## 8.6 Concentration

Concentration diagnostics should describe:

- Time
- Symbol
- Sector
- Market regime
- Direction
- Event type
- Decision time
- Parameter choice

Do not encode one universal concentration cutoff. Judge whether the concentration is compatible with the claimed mechanism and intended use.

---

# 10. Additional validation tools

## 9.1 Purging and embargo for overlapping labels

When target windows or holding periods overlap, ordinary random cross-validation can leak information across train and validation sets.

Use chronological splits. When necessary, purge observations whose label windows overlap the validation period and apply an embargo around fold boundaries.

The exact method should match the strategy and label construction rather than being applied mechanically.

## 9.2 Selection-adjusted performance

A selected strategy's observed Sharpe or return is biased upward when many variants were considered.

Useful diagnostics may include:

- Deflated Sharpe Ratio
- Probability of backtest overfitting
- Bootstrap confidence intervals
- White's Reality Check or related data-snooping adjustments
- Performance of the full tested family rather than only the winner

These tools do not replace judgment. They help describe how much confidence should be lost because of selection.

## 9.3 Dependence and effective sample size

Trades can be dependent because of:

- Overlapping holding periods
- Common market shocks
- Correlated symbols
- Repeated trades from one event
- Persistent regimes

Report dependence and, where useful, use clustered, block-bootstrap, HAC, or event-level methods.

Do not equate a large row count with a large amount of independent evidence.

## 9.4 Benchmarks and exposure

Compare strategies with relevant alternatives such as:

- Buy-and-hold benchmark
- Cash or risk-free return where appropriate
- Market- or sector-matched exposure
- Simple momentum or reversal baseline
- Parent strategy
- Equal-weight portfolio
- Existing candidate portfolio

A high net simple return caused mainly by market beta or leverage should be described accurately.

## 9.5 Data quality and reproducibility

Before interpreting performance, verify:

- Missing and duplicate bars
- Session completeness
- Timestamp timezone
- Split and dividend handling
- Delisted securities
- Stale or crossed quotes
- Corporate-action timing
- Membership coverage
- Deterministic configuration and random seeds
- Code commit and data fingerprint

A strategy result is not stronger than its underlying data and reproduction path.

## 9.6 High-return plausibility

Extremely high monthly simple returns often require leverage, concentration, unusually frequent opportunities, or severe tail risk.

Do not reject such objectives automatically, but reconcile the claimed return with:

- Time in market
- Average and maximum capital utilization
- Gross and net exposure
- Number of independent opportunities
- Turnover and costs
- Capacity
- Leverage
- Worst losses
- Concentration
- Length and representativeness of the sample

Use actual fixed-capital monthly and calendar-year P&L returns.

Do not convert a short exceptional sample into CAGR or a compounded future account projection when ranking the strategy.

# 11. Source hierarchy

## Primary sources

Examples:

- Academic papers
- Exchange and regulator documentation
- Official company filings
- Original datasets
- Broker or market-data documentation

Use for precise mechanics and factual claims.

## Practitioner research

Examples:

- Quant firm articles
- Institutional research
- Detailed independent replications
- Technical blogs with code and data

Use as evidence and implementation guidance, while checking incentives and methodology.

## Informal sources

Examples:

- Reddit
- X posts
- Discord discussions
- Trader interviews
- YouTube
- Anecdotal strategies

Use as creative hypothesis sources.

Extract the claim, mechanism, timing, universe, and falsification test. Do not copy a claimed return as evidence.

---


# 12. Reusable failure patterns

## Large blind parameter scans

Risk:

A large grid can discover a historically attractive configuration without revealing a stable mechanism.

Better approach:

Start with a small set of meaningful alternatives. Expand only when the first result raises a specific question.

## Retrospective filters

Risk:

Adding conditions after seeing losing periods can manufacture a strategy that explains the past but not the future.

Better approach:

State what behavior the filter represents and predict where else it should help.

## Precise stop optimization

Risk:

The selected stop may simply remove the largest historical loss.

Better approach:

Diagnose the loss path, test a few broad stop families, and examine nearby values and independent periods.

## Removing bad years

Risk:

This hides regime dependence and makes the evidence circular.

Better approach:

Study the regime, explain why the strategy should be active or inactive using information available at the time, and test that rule broadly.

## Current-member universes

Risk:

Using today's index members historically creates survivorship and selection bias.

Better approach:

Use point-in-time membership or clearly label the limitation.

## Unrealistic passive fills

Risk:

Quote touches overstate fills and ignore queue position.

Better approach:

Use trades, depth, conservative fill assumptions, and sensitivity analysis.

## Extrapolating tiny samples

Risk:

A short exceptional period can produce an impressive monthly simple return that may not persist.

Better approach:

Show the actual fixed-capital period return, event count, utilization, concentration, and uncertainty. Do not rank the strategy using CAGR or a compounded projection from the short sample.

## Treating the best variant as the hypothesis

Risk:

After a large search, the selected implementation appears more premeditated than it was.

Better approach:

Record the entire search family and treat the final strategy as adapted evidence requiring stronger confirmation.

---

# 13. Open research directions

These are generic examples of research questions, not inherited priorities, findings, or approved strategies.

The agent should choose among them only after reviewing the repository, data, outside evidence, and current objective. It may select entirely different questions.

- Does opening-gap behavior persist for several days when the first hour confirms the gap?
- Can volatility scaling improve recent continuation strategies without removing their strongest return periods?
- Do overnight and regular-session momentum represent distinct mechanisms?
- Are recent changes in index concentration, options activity, or ETF flows associated with stronger short-horizon continuation?
- Can a low-drawdown breadth or residual strategy be broadened and scaled into a useful sleeve?
- Are there weekly cross-sectional strategies that combine momentum, recovery, and volatility without excessive turnover?
- Can event-based filtering improve a mechanism without becoming retrospective period selection?
- Which intraday signals remain useful when expressed over longer horizons to reduce costs?
- Can strategies with different failure regimes be combined to improve total portfolio behavior?

Update this section as the project learns.
