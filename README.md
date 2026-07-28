# Quant Pipeline V2 Research Documents

This package defines a lightweight, adaptive research workflow for one Codex research lead.

## Permanent research documents

The local data locations and date boundaries are recorded in `research/DATA_SOURCES.yaml`. The data remains outside this public repository.

Reference source code and tests copied from the existing local projects are under `reference_code/`. Read `research/CODE_SOURCES.yaml` and `reference_code/README.md` before reusing them. Results, reports, runs, caches, credentials, and generated outputs were not copied.

- `AGENTS.md` — operating instructions
- `research/CONSTITUTION.md` — research principles and hard constraints
- `research/CURRENT.md` — active objectives and strategy profiles
- `research/KNOWLEDGE.md` — neutral starting knowledge
- `research/LEDGER.md` — one concise row per campaign

## Campaign template

```text
campaigns/_TEMPLATE/
├── PLAN.yaml
├── WORKLOG.jsonl
├── RESULTS.yaml
├── REVIEW.md
├── runs/
│   └── _RUN_TEMPLATE.yaml
└── artifacts/
    └── .gitkeep
```

## How it works

One coherent strategy idea is one campaign.

`PLAN.yaml` freezes the broad hypothesis, mechanism, starting implementation, hard constraints, and adaptation boundaries.

Codex may then perform many justified runs inside the campaign. Each run needs only a compact YAML record containing:

- Configuration
- Parent run
- Reason for the change
- Expected effect
- Factual result
- Decision

`WORKLOG.jsonl` is the concise chronological index of those runs.

A full `REVIEW.md`, campaign `RESULTS.yaml` update, and ledger update are required only at meaningful checkpoints—not after every adjustment.

Create a new campaign only when the underlying hypothesis, mechanism, information set, or strategy identity changes materially.

## Research accounting

- Fixed normalized capital base of `1.0`
- No reinvestment of prior P&L
- Additive simple P&L
- Equity curve equals `1.0 + cumulative net P&L`
- Drawdown is measured from the running equity peak
- Actual account size is a later deployment question

## Short-selling rule

Direct stock or ETF shorts:

- Must be strictly intraday
- Must have a protective stop or emergency exit
- Must be forcibly closed before the regular-session end
- Must use realistic exit and slippage assumptions
- May be used in intraday market-neutral, pairs, relative-value, or statistical-arbitrage strategies
- May not be carried overnight

## Clean baseline

The ledger contains no inherited strategy findings. Older work may be used as a lead only after its underlying evidence is checked.

Use `CODEX_SETUP_PROMPT.md` to create the folder structure. The setup run should not start research.
