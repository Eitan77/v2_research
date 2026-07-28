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
