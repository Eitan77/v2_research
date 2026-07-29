# Quant Pipeline Campaign Ledger

## Purpose

This is the concise index of V2 research campaigns.

Start clean. Do not preload conclusions from earlier chats, reports, or pipeline summaries.

The detailed chronology of a campaign belongs in its `WORKLOG.jsonl` and run files. The ledger should contain one concise row per campaign and should be updated only at meaningful checkpoints.

## Judgment vocabulary

Optional descriptive labels include:

- `active`
- `needs_targeted_diagnosis`
- `mechanism_unsupported`
- `implementation_failed`
- `economically_weak`
- `cost_sensitive`
- `execution_uncertain`
- `recent_regime_candidate`
- `overall_strategy_candidate`
- `low_risk_sleeve_candidate`
- `freeze_for_confirmation`
- `forward_test`
- `retired`

These are descriptions, not automatic classifications.

## Campaigns

| ID | Started | Title | Intended profile | Current status | Current best factual result | Next question |
|---|---|---|---|---|---|---|
| CAM-0001 | 2026-07-28 | Recent leveraged-ETF directional persistence | Recent Money Printer | `development_complete_forward_confirmation_required` | Adapted SMA20-rising TQQQ/SOXL continuation: 15m 8.59% average month, 12.70% DD, 85d recovery; 12m 10.55%, but 98.7th-percentile endpoint, top-five decisions 87%, SIP NBBO 0/45 | Acquire complete pre-cutoff ETF NBBO or observe the frozen rule prospectively; no further in-sample tuning |
| CAM-0002 | 2026-07-28 | Extreme intraday sell-shock reversal | Recent Money Printer | `retired_economically_weak_for_strategy_a` | Adapted 15m residual-stock reclaim +4% target: 15m 1.58% average month, 2.33% DD, 91d recovery, but only 13 events and 8/15 zero months; source baseline lost | Preserve the residual-reclaim lesson; broader Strategy A continues in CAM-0003 |
| CAM-0003 | 2026-07-28 | Morning-information to closing-flow intraday momentum | Recent Money Printer | `retired_recent_source_mechanism_inverted` | Source SPY long lost 9.21%; all 54 timing cells lost; zero latest-12m winners across 810 ETF/state cells; protected source shorts and causal magnitude tiers also lost | Preserve decay/inversion lesson; broader Strategy A continues in a materially different campaign |
| CAM-0004 | 2026-07-29 | Cross-sectional intraday factor-residual liquidity provision | Recent Money Printer | `retired_adapted_branch_exact_source_blocked` | All unconditional proxy variants lost gross; high-vol cumulative long-low rebound peaked at 0.95%/month net at 1bp/side with five negative months, failed protected short, and 133.5% top-five-day profit share | Preserve conditional lesson; exact paper needs unavailable source inputs; continue Strategy A with a materially different mechanism |
| CAM-0005 | 2026-07-29 | Late-day imbalance to overnight ETF gap | Recent Money Printer | `development_sleeves_frozen_not_type_a` | q60 SMH reversal passed 134/134 marketable SIP replays; capped volume/inverse sizing averaged 5.04%/month at 5bp extra slippage with 5.91% DD and 5d recovery, but older high-volume context had 36.24% DD and no causal regime onset | Paper-observe frozen sleeves only; no more tuning or holdout access; continue Strategy A with a materially different mechanism |
| CAM-0006 | 2026-07-29 | Opening-auction pressure absorption and intraday path | Recent Money Printer | `development_sleeve_frozen_not_type_a` | Fully covered marketable-NBBO high-volatility absorption sleeve: 5.53%/month over 15m, 9.83% DD, 5d recovery, 94 events/59 symbols, three positive blocks; bootstrap median 4.34% and only 0.05% reach 10% | Paper-observe frozen sleeve only; no more tuning, holdout, live allocation, or capacity claim; continue with a different mechanism |
| CAM-0007 | 2026-07-29 | Earnings-announcement reaction and post-event drift | Recent Money Printer | `retired_genuine_modest_event_premium_not_type_a` | Adapted combined after-close continuation/high-vol reclaim: 3.16%/month over 15m at 10bp, 14.43% DD, 87 trades/59 symbols; all LOO positive but 0/20,000 recent bootstraps reached 10% average month | Preserve event-state lessons; no quote replay, holdout, or live allocation; continue with a materially different mechanism |
| CAM-0008 | 2026-07-29 | Public analyst-revision drift and failed-revision response | Recent Money Printer | `retired_genuine_modest_analyst_revision_premium_not_type_a` | Failed-negative one-minute reaction +3m delay: 2.63%/month over 15m at 10bp, 17.70% DD, 557 trades/108 symbols; all tested removals positive but 0/20,000 recent bootstraps reached 10% | Preserve revision-rejection lesson; no quote replay, holdout, live allocation, or capacity claim; continue with a materially different mechanism |
| CAM-0009 | 2026-07-29 | Intraday semiconductor information-lead propagation | Recent Money Printer | `retired_genuine_common_sector_drift_not_type_a` | Corrected 20%-cap close-drift profile: 4.21%/month over 15m at 5bp, 10.71% DD, 1,918 trades; all leader/peer removals positive, but best month 29.73%, hedges fail, and only 2/20,000 bootstraps reach 13% | Preserve common-sector drift lesson; no quote replay, holdout, live allocation, or capacity claim; continue with a materially different mechanism |

## New campaign row

```markdown
| CAM-XXXX | YYYY-MM-DD | Campaign title | profile | `status` | Net simple return and standard maximum drawdown | Most important next question |
```

## Update policy

- Add a row when a campaign begins.
- Update the same row at meaningful checkpoints.
- Do not add a ledger row for every run.
- Preserve the detailed run history inside the campaign.
- Add reusable findings to `KNOWLEDGE.md` only when they generalize beyond one run.

## Reviewing prior work

Older artifacts may be used as leads, but do not copy their conclusions into this ledger.

When prior work becomes relevant:

1. Identify the underlying code, configuration, data, and outputs.
2. Verify the relevant result.
3. Create a V2 campaign or run that records the fresh judgment.
4. Add only the newly supported conclusion.

## Strategy lineage

Use this section when multiple campaigns contribute to one strategy or sleeve.

### STRAT-XXXX — Strategy name

- Originating campaign:
- Current campaign and run:
- Intended profile:
- Strongest supporting evidence:
- Strongest weakness:
- Validation status:
- Next unresolved question:
