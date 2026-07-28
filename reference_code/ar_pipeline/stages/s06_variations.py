from __future__ import annotations

import pandas as pd

from ar_pipeline.engines.robustness import run_robustness_review
from ar_pipeline.manifest import RunContext
from ar_pipeline.validation import SafetyGateError


def run(ctx: RunContext) -> dict[str, str]:
    """Run chronological robustness checks instead of launching a new search."""

    out = ctx.stage_dir(6)
    leaderboard_path = ctx.stage_dir(2) / "leaderboard.parquet"
    if not leaderboard_path.exists():
        raise FileNotFoundError("Stage 2 leaderboard is required for the Stage 6 trial ledger")
    quote_path = ctx.stage_dir(4) / "quote_filled_trades.parquet"
    if not quote_path.exists():
        raise FileNotFoundError("Stage 4 SIP quote-path ledger is required before robustness review")
    trades = pd.read_parquet(quote_path)
    if "quote_fill_mode" in trades.columns and trades["quote_fill_mode"].astype(str).eq("source_proxy_test_only").any():
        raise SafetyGateError("Stage 6 robustness cannot use source-proxy evidence; run real SIP quote-path validation")
    if "quote_fill_status" in trades.columns:
        trades = trades[trades["quote_fill_status"].astype(str).eq("filled")].copy()
    if "quote_fill_promotable" in trades.columns:
        trades = trades[trades["quote_fill_promotable"].astype(bool)].copy()
    if trades.empty or "quote_return" not in trades.columns:
        raise SafetyGateError("Stage 6 robustness requires complete promotable quote-filled trades")
    return run_robustness_review(
        trades,
        pd.read_parquet(leaderboard_path),
        out,
        return_col="quote_return",
        config=ctx.config,
    )
