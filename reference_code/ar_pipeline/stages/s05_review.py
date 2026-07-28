from __future__ import annotations

import pandas as pd

from ar_pipeline.engines.portfolio_validation import PRIMARY_VARIANT, write_portfolio_validation
from ar_pipeline.manifest import RunContext


def run(ctx: RunContext) -> dict[str, str]:
    src = ctx.stage_dir(4) / "source_vs_quote.csv"
    if not src.exists():
        raise FileNotFoundError("Stage 4 quote fill summary is required before post-fill review")
    out = ctx.stage_dir(5)
    out.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(src)
    portfolio = _quote_portfolio_validation(ctx, out)
    if not portfolio.empty:
        df = df.merge(portfolio, on="candidate_id", how="left")
    min_quote_total = float(ctx.config.get("post_fill", {}).get("min_quote_total", 0.0))
    min_quote_simple_return = float(ctx.config.get("post_fill", {}).get("min_quote_portfolio_simple_return", 0.0))
    min_quote_deployed_return = float(ctx.config.get("post_fill", {}).get("min_quote_return_on_deployed_capital", 0.0))
    min_active_days = float(ctx.config.get("post_fill", {}).get("min_active_days", 25))
    max_gap = float(ctx.config.get("post_fill", {}).get("max_avg_source_quote_gap_abs", 0.01))
    min_fill_rate = float(ctx.config.get("post_fill", {}).get("min_fill_rate", 1.0))
    quote_col = "quote_total_filled_only" if "quote_total_filled_only" in df.columns else "quote_total"
    gap_col = "avg_gap_filled_only" if "avg_gap_filled_only" in df.columns else "avg_gap"
    promotable_evidence = df.get("quote_evidence_promotable", pd.Series(False, index=df.index)).astype(bool)
    fill_rate = pd.to_numeric(df.get("fill_rate", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0.0)
    df["raw_quote_gate_pass"] = (
        (df[quote_col] >= min_quote_total)
        & (df[gap_col].abs() <= max_gap)
        & (fill_rate >= min_fill_rate)
        & promotable_evidence
    )
    df["post_fill_gate_pass"] = _post_fill_portfolio_gate(
        df,
        min_simple_return=min_quote_simple_return,
        min_return_on_deployed=min_quote_deployed_return,
        min_active_days=min_active_days,
        max_gap=max_gap,
        gap_col=gap_col,
        min_fill_rate=min_fill_rate,
    )
    df["quote_fill_flags"] = df.apply(
        lambda row: _quote_fill_flags(row, quote_col=quote_col, gap_col=gap_col, min_fill_rate=min_fill_rate), axis=1
    )
    df["agent_tracking_status"] = "awaiting_agent_verdict"
    df["agent_review_checklist"] = (
        "quote_collapse_checked=yes; quote_portfolio_validation_checked=yes; overlap_checked=yes; "
        "full_quote_needed=unknown; yearly_checked=yes; concentration_checked=yes; execution_filter_checked=no"
    )
    df["next_required_action"] = df.apply(lambda row: _next_required_action(row, quote_col=quote_col), axis=1)
    df["agent_decision"] = "needs_agent_review"
    df["agent_rationale"] = ""
    df.to_csv(out / "post_fill_review_queue.csv", index=False)
    report = out / "post_fill_review.md"
    report.write_text(
        "\n".join(
            [
                "# Stage 5 Post-Fill Review",
                "",
                "This queue is not a final verdict. The agent reviews quote collapse, portfolio realism, concentration, and trade plausibility here.",
                "Raw quote-filled compounding is diagnostic only. Post-fill gates require quote-filled portfolio validation.",
                "Rows include quote_fill_flags, next_required_action, agent_tracking_status, and agent_review_checklist for the manual review trail.",
                "",
                "```text",
                df.sort_values(["post_fill_gate_pass", "quote_portfolio_simple_total_return"], ascending=False).to_string(index=False),
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "review_queue": str(out / "post_fill_review_queue.csv"),
        "quote_portfolio_validation": str(out / "quote_portfolio_validation.csv"),
        "report": str(report),
    }


def _quote_fill_flags(row: pd.Series, *, quote_col: str, gap_col: str, min_fill_rate: float) -> str:
    flags: list[str] = []
    fill_rate = float(row.get("fill_rate", 0.0) or 0.0)
    quote_total = float(row.get(quote_col, 0.0) or 0.0)
    avg_gap = float(row.get(gap_col, 0.0) or 0.0)
    sampled = float(row.get("sampled_trades", row.get("trades", 0.0)) or 0.0)
    filled = float(row.get("filled_trades", sampled) or 0.0)
    if fill_rate < min_fill_rate:
        flags.append("missing_quotes")
    if not bool(row.get("quote_evidence_promotable", False)):
        flags.append("nonpromotable_quote_evidence")
    if str(row.get("quote_mode", "")).startswith("source_proxy"):
        flags.append("proxy_quote_evidence")
    if quote_total < 0:
        flags.append("quote_collapse")
    elif quote_total > 1.0:
        flags.append("strong_quote_survivor")
    else:
        flags.append("positive_quote_survivor")
    if abs(avg_gap) > 0.0025:
        flags.append("large_source_quote_gap")
    if sampled and filled < sampled:
        flags.append("partial_fill_sample")
    if _to_float(row.get("quote_portfolio_events", 0.0), 0.0) <= 0:
        flags.append("quote_portfolio_validation_missing")
    else:
        simple_return = _to_float(row.get("quote_portfolio_simple_total_return", 0.0), 0.0)
        if simple_return <= 0:
            flags.append("quote_portfolio_not_profitable")
        gate_flags = str(row.get("quote_portfolio_gate_flags", "") or "")
        if gate_flags and gate_flags != "ok":
            flags.extend([x for x in gate_flags.split(",") if x])
    return ",".join(flags)


def _next_required_action(row: pd.Series, *, quote_col: str) -> str:
    quote_total = float(row.get(quote_col, 0.0) or 0.0)
    sampled = float(row.get("sampled_trades", row.get("trades", 0.0)) or 0.0)
    filled = float(row.get("filled_trades", sampled) or 0.0)
    if quote_total < 0:
        return "reject_or_diagnose_quote_collapse"
    portfolio_events = _to_float(row.get("quote_portfolio_events", 0.0), 0.0)
    portfolio_return = _to_float(row.get("quote_portfolio_simple_total_return", 0.0), 0.0)
    if portfolio_events > 0 and portfolio_return <= 0:
        return "reject_quote_portfolio_unprofitable"
    if sampled and sampled <= 250 and quote_total > 0:
        return "run_full_quote_fill_before_oos"
    if filled > 0 and quote_total > 0:
        return "portfolio_yearly_concentration_audit"
    return "agent_review_required"


def _quote_portfolio_validation(ctx: RunContext, out) -> pd.DataFrame:
    trades_path = ctx.stage_dir(4) / "quote_filled_trades.parquet"
    if not trades_path.exists():
        return pd.DataFrame()
    trades = pd.read_parquet(trades_path)
    if "quote_return" not in trades.columns:
        return pd.DataFrame()
    if "quote_fill_status" in trades.columns:
        trades = trades[trades["quote_fill_status"].astype(str).str.lower().eq("filled")].copy()
    if "quote_fill_promotable" in trades.columns:
        trades = trades[trades["quote_fill_promotable"].astype(bool)].copy()
    trades = trades[pd.to_numeric(trades["quote_return"], errors="coerce").notna()].copy()
    if trades.empty:
        return pd.DataFrame()
    validation = write_portfolio_validation(trades, out, return_col="quote_return", prefix="quote_portfolio_validation")
    primary = validation[validation["portfolio_variant"].eq(PRIMARY_VARIANT)].copy()
    if primary.empty:
        return pd.DataFrame()
    rename = {
        "portfolio_events": "quote_portfolio_events",
        "simple_total_return": "quote_portfolio_simple_total_return",
        "return_on_deployed_capital": "quote_return_on_deployed_capital",
        "compounded_total_x": "quote_portfolio_compounded_total_x",
        "compounded_cagr": "quote_portfolio_compounded_cagr",
        "max_drawdown": "quote_portfolio_max_drawdown",
        "win_rate": "quote_portfolio_win_rate",
        "active_days": "quote_portfolio_active_days",
        "max_same_day_signals": "quote_portfolio_max_same_day_signals",
        "max_concurrent_positions": "quote_portfolio_max_concurrent_positions",
        "top1_day_log_share": "quote_portfolio_top1_day_log_share",
        "top5_day_log_share": "quote_portfolio_top5_day_log_share",
        "positive_years": "quote_portfolio_positive_years",
        "years_tested": "quote_portfolio_years_tested",
        "worst_year_simple_return": "quote_portfolio_worst_year_simple_return",
        "portfolio_gate_flags": "quote_portfolio_gate_flags",
    }
    keep = ["candidate_id"] + [c for c in rename if c in primary.columns]
    return primary[keep].rename(columns=rename)


def _post_fill_portfolio_gate(
    df: pd.DataFrame,
    *,
    min_simple_return: float,
    min_return_on_deployed: float,
    min_active_days: float,
    max_gap: float,
    gap_col: str,
    min_fill_rate: float,
) -> pd.Series:
    if "quote_portfolio_events" not in df.columns:
        return pd.Series(False, index=df.index)
    return (
        (pd.to_numeric(df["quote_portfolio_events"], errors="coerce").fillna(0) > 0)
        & (pd.to_numeric(df["quote_portfolio_active_days"], errors="coerce").fillna(0) >= min_active_days)
        & (pd.to_numeric(df["quote_portfolio_simple_total_return"], errors="coerce").fillna(-999) > min_simple_return)
        & (pd.to_numeric(df["quote_return_on_deployed_capital"], errors="coerce").fillna(-999) > min_return_on_deployed)
        & (pd.to_numeric(df[gap_col], errors="coerce").fillna(999).abs() <= max_gap)
        & (pd.to_numeric(df.get("fill_rate", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0.0) >= min_fill_rate)
        & df.get("quote_evidence_promotable", pd.Series(False, index=df.index)).astype(bool)
    )


def _to_float(value: object, default: float) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
