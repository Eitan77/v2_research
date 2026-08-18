# Current Research Direction

## 2026-08-18 checkpoint — EPDC rejection and sparse ETF reversal paper candidate

CAM-0631 confirmed a reusable passive-toxicity signal but rejected historical execution. Conservative same-price queue conditioning reversed the favorable ex-ante markout, and none of 1,296 prespecified early-development execution repairs passed the minimum-fill and two-block gate. The untouched late validation block remained unopened. Any continuation requires prospective broker-specific queue calibration, not historical threshold rescue.

CAM-0632 rejected dense leveraged-ETF scalping after friction and late validation, but found two sparse large-shock reversal rules that survived exact marketable SIP replay and adversarial audit. A cash-only 50/50 portfolio was then capacity-capped using only entry-time displayed depth. For a $2,000 reference account, the 1,000 ms plus 5 bp/side path returned +42.31% additive with 5.89% drawdown and only +0.20% in the latest twelve months. Adding 25 bp to all depth-unsupported exits retained +33.79% with all chronology blocks positive; 50 bp broke the first block.

The exact adapted rules and 5% entry-depth sizing are frozen in `campaigns/CAM-0632/FORWARD_PAPER_SPEC.md`. Independent code reproduced all 314 trades exactly, and the guarded no-order shadow runner passed synthetic timing/idempotency tests. The valid next step is unchanged forward paper observation for the later of twelve months or fifty trades. Dense daily-printer and scalable-account claims are rejected; sealed post-April-2026 history remains untouched.

## 2026-08-10 corrected checkpoint — reciprocal split repair

The prior SSRN and CAM-0625 checkpoint is invalid. The inherited stock panel
applied forward split share multipliers directly to historical prices rather
than reciprocally; NVDA's June 2024 split appeared as an approximately −99%
overnight return. Repaired lineage is CAM-0600 through CAM-0624 RUN-0020/RUN-0021
and quote RUN-0023. All prior artifacts are preserved but must not be interpreted.

Under corrected data, 23 of 25 families have a structured quote-positive survivor;
CAM-0606 pairs and CAM-0613 support/resistance do not. These are overlapping,
adapted research leads, not independent deployable strategies.

The best current construction is CAM-0625's equal-weight final substitution:
CAM-0600 momentum, CAM-0621 ETF IBS, CAM-0624 volatility-managed safest-distress,
and CAM-0618 sector rotation. It has +153.6% full-history additive return with
11.5% drawdown, and +40.0% in the 2025-05-01 through 2026-04-30 09:40 SIP replay
at +2 bp/side with 7.25% drawdown and 10/2 months. It survives +10 bp stress at
+37.3%.

Nothing is promoted. The frozen pre-2024 selector identifies only ETF IBS, the
final ensemble is adaptively selected, and bootstrap tails remain negative.
The sealed holdout remains untouched. The only valid next step is unchanged
small-scale forward paper tracking.

## 2026-08-10 checkpoint — SSRN deep development and CAM-0625 ensemble

CAM-0600 through CAM-0624 completed a second mechanism-driven development
loop, corrected target-change SIP replay, matched simpler controls, attrition,
concentration, delay, cost, and correlation audits. The prior daily-reset quote
replay was a harsh stress, not a correct hold-shares execution model; a later
raw-quote/split-adjusted-reference mismatch was also found and preserved as
invalid before interpretation.

CAM-0625 is the best current development lead. It combines four whole,
low-correlation sleeves from CAM-0600, CAM-0604, CAM-0621, and CAM-0624. Equal
weight returned +52.6% in corrected 09:40 target-change SIP replay from
2025-05-01 through 2026-04-30 after 2 bp additional slippage per side, with
5.6% drawdown and 10/2 positive/negative months. Causal inverse volatility
returned +40.0% with 4.3% drawdown and 11/1 months. Both survived 10-bp-extra
stress; every leave-one-sleeve-out path remained profitable.

The latest 12/18/24-month ensemble performance is at the top historical
percentile and substantially stronger than the earliest chronological fold.
Treat it as a recent-regime development lead, not a permanent return rate.
Nothing is promoted. The sealed holdout remains untouched. The next valid
step is unchanged forward paper tracking with the frozen responsive six-month
decay monitor; any sealed-holdout access requires explicit authorization and a
separately frozen purpose.

## 2026-08-06 checkpoint — SSRN 25-strategy series complete

CAM-0600 through CAM-0624 completed the requested source baselines,
mechanism-driven adaptations, development-only robustness, and applicable SIP
quote gates. No campaign is promoted and no May 2026-or-later holdout data was
accessed.

The two strongest complete quote-gated development candidates are CAM-0600 ETF
momentum and CAM-0622 QQQ/BIL volatility targeting. CAM-0604 multifactor,
CAM-0607 daily ETF cluster reversal, and CAM-0623 safest-distress remain
profitable but fragile or incomplete leads. The other campaigns are retired,
execution-sensitive, or stopped as non-executable signed signals. The full
checkpoint is `campaigns/CAM-0600/COMPREHENSIVE_REPORT.md`.

No automatic research or holdout evaluation is active. Any forward paper test
or sealed-holdout use requires a separately frozen, explicitly authorized
purpose with unchanged rules.


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

### Hard process requirement

The agent must not stop at a few broad variants or substitute its default
research habits for the documented workflow. For every active campaign:

- Build and use a checklist of the applicable governing instructions.
- Verify any source-faithful baseline before interpreting it.
- Diagnose weak results and attempt reasonable mechanism-consistent
  improvements.
- Investigate relevant timeframes, universes, assets, confirmations, filters,
  indicators, sizing, risk, and execution choices.
- Analyze results by period, symbol, event, leg, and portfolio component.
- Treat concentration as both a risk and a possible source of a specialized
  edge.
- Test whether profitable stocks or events can be selected causally.
- Report date, symbol, event, and row attrition caused by new fields,
  indicators, joins, filters, or completeness requirements.
- Stop execution when any test, schema, readiness, timing, or holdout
  validation fails.
- Reconcile the planned configuration with the actual command, resolved
  defaults, executed variant count, and saved outputs before interpretation.
- Do not promote, retire, or pivot while an obvious principled experiment
  remains.

The strategy may trade a single stock, a fixed stock set, a causal ranking
universe, a changing point-in-time subset, ETFs, leveraged or inverse ETFs, a
hybrid expression, or remain in cash. Select intelligently based on the
mechanism and maximize credible net fixed-capital profit after realistic costs,
risk, stability, capacity, and execution. Do not require broad participation
when a concentrated edge is causally identifiable, and do not use full-sample
winners as if they had been known in advance.

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

**Recent-regime evaluation:** Class A is explicitly a temporary, recent
phenomenon profile. Judge its approximately 10% to 15% average-month objective
over a representative recent period normally spanning **about 12 to 18
months** before the sealed holdout. Twelve, fifteen, and eighteen months are
useful views, but none is a hard cutoff or automatic pass/fail gate. Use AI
judgment to decide whether the evidence reflects a genuine exploitable recent
regime, considering consistency, drawdown, recovery, breadth, concentration,
execution, and mechanism. A clearly justified causal regime-onset window
inside or near that band may be more informative than an arbitrary exact
endpoint; never choose the start merely because it maximizes historical
returns. Do not dilute the recent objective by averaging it over the entire
historical backtest. Older history remains mandatory context for mechanism,
tail risk, regime change, and failure diagnosis, but weak old performance does
not by itself disqualify a genuine recent money printer. A Class A claim must
define causal activation, decay monitoring, and retirement logic because the
opportunity is expected to be temporary.

Reference objectives:

- Average monthly net simple return over a representative recent 12-to-18-month
  regime: approximately **10% to 15%**, interpreted as a reference objective
  rather than a rigid numerical gate
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
| Recent Money Printer | Consistent exceptional recent income | Approximately 10%–15% monthly over a representative recent 12-to-18-month regime; AI judgment, not a hard gate | Below 20% |
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
- Cache shared data, batch related experiments, and keep narration sparse to
  conserve the user's limited token and compute budget. Efficiency must not
  become incomplete research.
- Before any checkpoint, promotion, retirement, or pivot, complete the
  conclusion audit in `AGENTS.md` and `research/CONSTITUTION.md`.

## Active V2 campaign

`CAM-0001` completed in-sample development of recent leveraged-ETF directional
persistence. Its adapted frozen specification is TQQQ/SOXL equal fixed-base
weights when each has positive five-session momentum and QQQ is above a rising
SMA20, entered next open and exited ten sessions later.

It is not promoted. The neutral 15-month view averaged 8.59% net simple return
per month with 12.70% drawdown and 85-day recovery; the favorable 12-month view
averaged 10.55% but was a 98.7th-percentile rolling endpoint and concentrated
in 19 decisions. SIP minute-bar latency held up, but required ETF SIP NBBO
coverage was 0/45. Status is
`development_complete_forward_confirmation_required`: do not tune further;
obtain complete pre-cutoff ETF NBBO or collect genuinely prospective paper
evidence. The sealed interval beginning 2026-05-01 remains inaccessible.

`CAM-0002` completed and is retired for Strategy A. Its source-faithful
intraday reversal lost money. A heavily adapted 15-minute residual-stock
reclaim with a 4% target averaged 1.58% net simple return per month over the
neutral 15-month view with 2.33% drawdown, but only 13 events and eight zero
months. Lower shock thresholds increased frequency and destroyed the edge.
No quote budget or holdout data was used; the sealed interval beginning
2026-05-01 remains untouched.

`CAM-0003` completed and is retired. Its source-faithful positive-morning SPY
continuation lost 9.21%; all 54 timing/confirmation cells lost, zero of 810
ETF/state cells survived the latest 12 months, and protected source-direction
shorts also lost. The recent relationship is inverted rather than hidden by
timing, source-emphasized ETF coverage, opening activity, or signal magnitude.
No quote/auction budget or sealed data was used.

`CAM-0004` completed and its adapted QQQ proxy branch is retired. Exact
Brogaard-Han-Kim replication remains blocked by unavailable point-in-time
S&P 500 June membership, exact causal characteristics, and source-equivalent
midpoints; it was not silently proxied and the paper itself was not disproven.
All unconditional adapted reversal variants lost gross. A prespecified
high-volatility cumulative-residual state recovered a modest long-low rebound,
but every protected short neighborhood lost. After concurrent-capital scaling,
the best bounded residual model averaged only 0.95% net per month at 1 bp/side,
had five negative months and 6.46% drawdown, and its top five profitable days
exceeded total profit. No quote or sealed data was used.

The broader Strategy A search is not exhausted. Begin a materially different
campaign; do not retune CAM-0001 through CAM-0004 without genuinely new causal
information or newly declared exact-source inputs.

`CAM-0005` is complete with two frozen development sleeves, neither Type A.
The causal q60/edge25 late-SMH reversal passed marketable SIP replay on all 134
events. A capped rule that sizes low-volume events at 0.25 and SOXS at 0.75
averaged 5.04% net simple return per month at 5 bp additional slippage per
side, with 5.91% drawdown, five-day recovery, 13 positive and five negative
months, positive product legs, and all three six-month blocks positive.
However, it earns roughly half the reference return, remains adapted, and its
older high-volume bar context had 36.24% drawdown and a losing inverse leg.
No causal regime-onset rule survived. Status is
`development_sleeves_frozen_not_type_a`: paper-observe only, no more tuning,
no holdout access, and no capacity claim from unverified raw quote-size units.

`CAM-0006` is complete with one frozen development sleeve, not Type A. A
point-in-time QQQ long-stock rule combines official negative opening-gap
absorption, elevated auction size, high prior QQQ volatility, strongest
same-day reclaim, and strong first-minute participation. Fully covered
first-ask/first-bid SIP replay plus 2 bp additional slippage per side averaged
5.53% net simple return per month over 15 months with 9.83% drawdown, five-day
recovery, 94 events across 59 symbols, three positive six-month blocks, three
negative months, and three inactive months. Twenty thousand moving-block
samples had a 4.34% median average month and only 0.05% reached 10%. Status is
`development_sleeve_frozen_not_type_a`: paper-observe only; no more tuning,
holdout access, live allocation, or capacity claim.

`CAM-0007` is complete and retired for Strategy A. Public earnings reactions
revealed after-close positive continuation and high-volatility negative-gap
recovery, but the best overlap-aware combined book averaged only 3.16% per
month over 15 months at 10 bp per side with 14.43% drawdown. Every worst exact
symbol/event removal remained positive, yet zero of 20,000 recent moving-block
samples reached a 10% average month. No quote replay or holdout access was
used; execution work cannot rescue the missing alpha.

`CAM-0008` is complete and retired for Strategy A. Explicit public analyst
revisions produce a genuine failed-negative resilience premium: a one-minute
positive reaction, three-minute delay, and ten-session long exit averaged
2.63% per month over 15 months at 10 bp per side, with 17.70% drawdown, four
negative months, 557 trades, and 108 symbols. All 439 capacity-aware removal
tests remained positive, but zero of 20,000 recent block-bootstrap samples
reached a 10% average month. No quote replay or holdout data was used.

`CAM-0009` is complete and retired for Strategy A. Rapid 5-60-minute peer
propagation failed. A corrected slower unhedged close-drift profile averaged
4.21% per month over 15 months at 5 bp, with 10.71% drawdown and 11/15 positive
months, but April 2025 earned 29.73%, removing it cuts the average to 2.39%,
SMH hedges fail, and only 2/20,000 bootstrap paths reach 13%. No quote replay
or holdout data was used.

The broader active objective is to continue governed, materially distinct
campaigns until two or three genuine Type-A candidates survive causal timing,
fixed-base additive accounting, realistic net costs, monthly consistency,
drawdown/recovery, concentration, robustness, and execution evidence. A
campaign completion is not itself a candidate and does not end Strategy A.
