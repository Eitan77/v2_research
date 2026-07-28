from __future__ import annotations

from pathlib import Path

import pandas as pd

from ar_pipeline.approvals import approved_quote_candidates
from ar_pipeline.engines.quote_fill import run_quote_fill
from ar_pipeline.manifest import RunContext
from ar_pipeline.validation import SafetyGateError, validate_trade_ledger


def run(ctx: RunContext) -> dict[str, str]:
    """Validate exactly the signed Stage-3 trade intents on SIP quote paths."""

    out = ctx.stage_dir(4)
    input_trades = _promoted_trade_ledger(ctx, out)
    run_quote_fill(ctx.config, input_trades, out)
    return {
        "quote_trades": str(out / "quote_filled_trades.parquet"),
        "summary": str(out / "source_vs_quote.csv"),
        "report": str(out / "fill_report.md"),
    }


def _promoted_trade_ledger(ctx: RunContext, out: Path) -> Path:
    """Materialize the immutable, canonical trade ledger selected at Stage 3.

    Earlier versions attempted to recreate selected trades from random weights
    or strategy-specific shortcuts.  That can silently diverge from the bar
    screen that earned promotion, especially for stop/target strategies.  A
    quote path must instead consume the exact same intent ledger.
    """

    if int(ctx.config.get("schema_version", 0) or 0) < 2:
        raise SafetyGateError("legacy discovery ledgers cannot enter quote validation; start a schema_version=2 run")
    selected = approved_quote_candidates(ctx.run_path)
    discovery_trades = ctx.stage_dir(2) / "discovery_trades.parquet"
    if not discovery_trades.exists():
        raise FileNotFoundError("Stage 2 canonical discovery_trades.parquet is required before quote validation")
    trades = pd.read_parquet(discovery_trades)
    if "candidate_id" not in trades.columns:
        raise SafetyGateError("Stage 2 discovery ledger has no candidate_id")
    selected_trades = trades[trades["candidate_id"].astype(str).isin(selected)].copy()
    if selected_trades.empty:
        raise SafetyGateError("signed Stage 3 candidates have no matching Stage 2 trade intents")
    if "bar_fill_status" in selected_trades.columns:
        bad = ~selected_trades["bar_fill_status"].astype(str).eq("filled")
        if bad.any():
            raise SafetyGateError("quote validation refuses discovery candidates with unfilled bar intents")
    validate_trade_ledger(selected_trades)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "promoted_discovery_trades.parquet"
    selected_trades.to_parquet(path, index=False)
    return path


def _selected_candidate_ids(ctx: RunContext) -> set[str]:
    """Compatibility helper retained for integrations; never falls back to auto gates."""

    return approved_quote_candidates(ctx.run_path)
