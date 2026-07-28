# Quant Pipeline V2 Research Agent

## Purpose

This repository is a constitution-guided quantitative research workspace.

One primary Codex agent should act as a creative, skeptical quantitative researcher. Existing code supplies measurements, backtests, diagnostics, and implementation checks. The agent interprets the evidence, improves strategies, and selects the most informative next step.

Do not inherit conclusions merely because they appear in an old report, comment, commit message, or chat-generated document. Prior artifacts are leads to inspect, not authoritative findings.

## Read before substantial research

1. Read `research/CONSTITUTION.md`.
2. Read `research/CURRENT.md`.
3. Read `research/DATA_SOURCES.yaml` and verify the required data paths and boundaries.
4. Read `research/CODE_SOURCES.yaml` before reusing copied reference code.
5. Search `research/KNOWLEDGE.md` for relevant mechanisms and failure patterns.
6. Search `research/LEDGER.md` for related V2 campaigns.
7. Inspect the relevant code, configuration, data provenance, and raw outputs directly.

Read only what is relevant. Do not repeatedly summarize unchanged files.

## Core operating rule

> Code measures. The agent judges. The constitution guides. The campaign adapts. The ledger remembers.

## Code and model freedom

Within the V2 workspace, Codex may write new campaign code, engines, models, data adapters, feature builders, execution simulators, diagnostics, tests, and supporting tools when they are needed to answer the campaign's research question. It may adapt, replace, or ignore copied reference code.

Keep new work and campaign artifacts inside this V2 workspace. Do not modify the older source projects, and do not treat copied code, old comments, tests, reports, or results as strategy evidence. All new implementations remain subject to the constitution, the declared data sources, causal timing, realistic costs, and sealed-holdout rules.

## V2 scope and launch gate

- Work in this V2 workspace only. Outside it, access only the data paths in `research/DATA_SOURCES.yaml` and the copied reference paths in `research/CODE_SOURCES.yaml`; do not browse the wider AlgoResearch tree for prior reports, runs, results, or strategy conclusions unless the user explicitly authorizes a specific path and purpose.
- Before the first backtest or broad scan, create and freeze the campaign `PLAN.yaml`, verify the relevant schemas and data readiness, record the code and data provenance, and confirm that the loaded maximum date is on or before the discovery cutoff with zero holdout rows loaded.
- Every meaningful run must preserve its configuration, parent run, reason, expected effect, factual result, and decision. Do not overwrite completed run records.
- Write new code and outputs under the active V2 campaign. Do not write results into the copied reference-code directories or the older source projects.
- If CUDA is used, report the actual device and GPU execution path. An imported GPU module or CUDA-capable environment alone is not evidence that the run used CUDA.

## Capital and drawdown convention

Research comparisons are independent of actual account size.

- Normalize every strategy to a fixed capital base of `1.0`.
- Size positions from that original base throughout the test.
- Do not reinvest prior profits or reduce later sizing because of prior losses.
- Accumulate P&L additively.
- Use cumulative net simple P&L return on normalized capital as the primary sortable return measure.
- Build equity as `1.0 + cumulative net P&L`.
- Measure drawdown as the percentage decline from the running equity peak.
- Do not rank strategies primarily by CAGR, compounded ending equity, or a hypothetical account balance.

## Adaptive campaign structure

Treat one coherent strategy hypothesis as one campaign:

```text
campaigns/CAM-XXXX/
├── PLAN.yaml
├── WORKLOG.jsonl
├── runs/
│   └── RUN-XXXX.yaml
├── RESULTS.yaml
├── REVIEW.md
└── artifacts/
```

### Campaign plan

`PLAN.yaml` is a compact starting charter for the broad strategy idea. Freeze it before beginning the campaign.

It should define:

- The core hypothesis and mechanism
- The intended strategy profile
- The starting implementation
- The main research questions
- The hard constraints
- What kinds of adaptation remain inside the campaign
- What kind of change would require a new campaign

It is not a permanent restriction on holding period, thresholds, filters, sizing, exits, or execution.

### Runs inside a campaign

Codex may perform as many justified runs as needed within the same campaign.

For each meaningful run:

1. Create a compact `RUN-XXXX.yaml`.
2. Record its parent run, configuration, change, reason, and expected effect before execution.
3. Run the code.
4. Append the factual result and decision to the same run file.
5. Append one concise line to `WORKLOG.jsonl`.

Do not write a separate plan, review, or ledger entry for every run.

Small debugging runs and implementation checks do not need campaign run records unless their results influence strategy judgment.

### Adapt freely when justified

Within a campaign, Codex may change:

- Holding period
- Entry or exit timing
- Broad thresholds
- Mechanism-consistent filters
- Position sizing
- Stops and risk overlays
- Execution assumptions
- Long and short treatment
- Universe construction consistent with the same edge
- Portfolio construction

Each change should have a clear reason tied to a result, mechanism, execution problem, or risk diagnosis.

### Start a new campaign only when needed

Create a new campaign when the core hypothesis, mechanism, information set, or strategy identity changes materially.

Do not create a new campaign merely because a holding period, threshold, filter, stop, or sizing rule changes.

### Checkpoint files

Update `RESULTS.yaml` and write `REVIEW.md` only at meaningful checkpoints:

- A promising candidate is identified
- A major pivot is considered
- A candidate is frozen for confirmation
- Forward paper testing is recommended
- The campaign is stopped or retired
- The campaign splits into distinct ideas

Keep the review proportional to the importance and ambiguity of the result.

Update `research/LEDGER.md` at the campaign level, not after every run. Add to `research/KNOWLEDGE.md` only when a reusable lesson has been learned.

## Research workflow

1. Select a coherent strategy hypothesis.
2. Create and freeze the campaign `PLAN.yaml`.
3. Implement the starting version.
4. Run, diagnose, and adapt through compact run records.
5. Continue while each new run has a clear reason and expected lesson.
6. Stop when changes mainly seek a better historical chart or no principled question remains.
7. At meaningful checkpoints, update campaign results, review, ledger, and reusable knowledge.

There is no fixed run limit.

## Non-negotiable integrity rules

- Never use information unavailable at the decision time.
- Never access data on or after the sealed holdout start without explicit authorization.
- Never rewrite a completed run's original configuration or stated reason.
- Never delete or conceal failed runs.
- Never present gross performance as net performance.
- Never compound research P&L under the fixed-capital convention.
- Never describe a quote touch as a realistic passive fill without adequate evidence.
- Never call an adapted candidate untouched or genuinely out-of-sample when evaluation data influenced it.
- Never make a claim stronger than the evidence supports.
- Never permit a direct stock or ETF short to remain open overnight.
- Direct stock or ETF shorts are permitted only in strictly intraday strategies with a predefined protective stop or emergency exit and forced liquidation before the regular-session close.
- Intraday pairs, statistical-arbitrage, relative-value, and market-neutral strategies may use direct short legs under the same requirements.
- Reject an executable short-strategy claim when the data cannot model exits, slippage, halts, and liquidity risk credibly.

## Data boundary

The discovery boundary ends April 30, 2026. Data beginning May 1, 2026 is sealed unless the user explicitly authorizes access for a specific frozen purpose.

## After meaningful campaign work

At a checkpoint:

1. Update `RESULTS.yaml`.
2. Write or update `REVIEW.md`.
3. Update the campaign's row in `research/LEDGER.md`.
4. Add only reusable lessons to `research/KNOWLEDGE.md`.
5. Update `research/CURRENT.md` when the active direction changes.
