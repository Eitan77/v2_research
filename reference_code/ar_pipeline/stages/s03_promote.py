from __future__ import annotations

import pandas as pd

from ar_pipeline.engines.portfolio_validation import PRIMARY_VARIANT, write_portfolio_validation
from ar_pipeline.manifest import RunContext


def run(ctx: RunContext) -> dict[str, str]:
    src = ctx.stage_dir(2) / "leaderboard.parquet"
    cost_src = ctx.stage_dir(2) / "cost_sensitivity.csv"
    if not src.exists():
        raise FileNotFoundError("Stage 2 leaderboard is required before promotion review")
    out = ctx.stage_dir(3)
    out.mkdir(parents=True, exist_ok=True)
    gates = ctx.config.get("promotion", {})
    min_trades = float(gates.get("min_trades", 100))
    min_cagr = float(gates.get("min_cagr", 0.0))
    max_drawdown = float(gates.get("max_drawdown", -0.35))
    min_active_days = float(gates.get("min_active_days", 25))
    min_simple_return = float(gates.get("min_portfolio_simple_return", 0.0))
    min_return_on_deployed = float(gates.get("min_return_on_deployed_capital", 0.0))
    df = pd.read_parquet(src)
    if cost_src.exists():
        cost = pd.read_csv(cost_src)
        cost_cols = [
            "base_candidate_id",
            "max_profitable_cost_bps_per_side",
            "log_return_at_lowest_cost",
            "log_return_at_highest_cost",
            "return_at_lowest_cost",
            "return_at_highest_cost",
            "cagr_at_highest_cost",
            "worst_drawdown_across_costs",
        ]
        df = df.merge(cost[cost_cols], on="base_candidate_id", how="left")
    df["max_profitable_cost_bps_per_side"] = df.get("max_profitable_cost_bps_per_side", pd.Series(index=df.index, dtype=float)).fillna(-1.0)
    df["log_return_at_highest_cost"] = df.get("log_return_at_highest_cost", pd.Series(index=df.index, dtype=float)).fillna(0.0)
    df["raw_metric_gate_pass"] = (
        (df["trades"] >= min_trades)
        & (df["cagr"] >= min_cagr)
        & (df["max_drawdown"] >= max_drawdown)
    )
    initial_review = df.sort_values(
        ["raw_metric_gate_pass", "max_profitable_cost_bps_per_side", "log_return_at_highest_cost", "cagr"],
        ascending=False,
    ).head(int(gates.get("review_top", 50)) * int(gates.get("portfolio_prefilter_multiplier", 20))).copy()
    portfolio = _portfolio_validation_for_review(ctx, out, initial_review)
    if not portfolio.empty:
        df = df.merge(portfolio, on="base_candidate_id", how="left")
    else:
        for col in _portfolio_merge_columns():
            df[col] = pd.NA
    df["screening_eligibility"] = _portfolio_gate(
        df,
        min_trades=min_trades,
        min_active_days=min_active_days,
        min_simple_return=min_simple_return,
        min_return_on_deployed=min_return_on_deployed,
        max_drawdown=max_drawdown,
    )
    review = df.sort_values(
        ["screening_eligibility", "portfolio_simple_total_return", "portfolio_return_on_deployed_capital", "max_profitable_cost_bps_per_side"],
        ascending=False,
    ).head(int(gates.get("review_top", 50))).copy()
    review_ids = set(review["base_candidate_id"].astype(str))
    snapshots = _cost_grid_snapshots(df[df["base_candidate_id"].astype(str).isin(review_ids)])
    review["cost_grid_snapshot"] = review["base_candidate_id"].astype(str).map(snapshots).fillna("")
    review["promotion_flags"] = review.apply(
        lambda row: _promotion_flags(
            row,
            min_trades=min_trades,
            min_cagr=min_cagr,
            max_drawdown=max_drawdown,
            min_active_days=min_active_days,
            min_simple_return=min_simple_return,
            min_return_on_deployed=min_return_on_deployed,
        ),
        axis=1,
    )
    # This remains an eligibility queue.  A separate, fingerprinted approval
    # artifact is required before Stage 4 can spend time/API quota on quotes.
    review["auto_gate_pass"] = review["screening_eligibility"]
    review["agent_tracking_status"] = "awaiting_written_quote_fill_decision"
    review["agent_review_checklist"] = (
        "spec_interpreted=no; portfolio_validation_checked=yes; overlap_checked=yes; "
        "yearly_checked=no; cost_grid_checked=yes; concentration_checked=yes; "
        "duplicate_concept_checked=no; quote_fill_plan=no"
    )
    review["agent_decision"] = "needs_agent_review"
    review["agent_rationale"] = ""
    review["agent_reviewer"] = ""
    review["agent_reviewed_at"] = ""
    review.insert(0, "promotion_rank", range(1, len(review) + 1))
    review.to_csv(out / "promotion_review_queue.csv", index=False)
    eligible = review[review["screening_eligibility"]].copy()
    eligible.to_parquet(out / "screening_eligible_candidates.parquet", index=False)
    lines = [
        "# Stage 3 Promotion Review",
        "",
        "Automatic gates only create a screening-eligibility queue. They never promote a candidate or authorize a quote request.",
        "Raw signal compounding and slippage columns are evidence only. Automatic promotion now requires a capital-aware portfolio validation row.",
        "A 100% deployment strategy cannot promote overlapping full-size trades unless an explicit pyramiding capital model is supplied.",
        "Each row now includes promotion_flags, cost_grid_snapshot, agent_tracking_status, and agent_review_checklist so a written approval has an audit trail.",
        "Use `ar-pipeline approve-quote --run ... --candidates ... --rationale ...` after review. That command fingerprints scan.yaml and creates the required Stage 4 approval artifact.",
        "",
        f"Min trades: {min_trades}",
        f"Min CAGR: {min_cagr}",
        f"Max drawdown floor: {max_drawdown}",
        f"Min active days: {min_active_days}",
        f"Min portfolio simple return: {min_simple_return}",
        f"Min return on deployed capital: {min_return_on_deployed}",
        "",
        "```text",
        review.head(25).to_string(index=False),
        "```",
        "",
    ]
    report = out / "promotion_report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return {
        "review_queue": str(out / "promotion_review_queue.csv"),
        "screening_eligible": str(out / "screening_eligible_candidates.parquet"),
        "cost_sensitivity": str(cost_src) if cost_src.exists() else "",
        "portfolio_validation": str(out / "portfolio_validation.csv"),
        "report": str(report),
    }


def _cost_grid_snapshots(df: pd.DataFrame) -> dict[str, str]:
    required = {"base_candidate_id", "cost_bps_per_side", "cagr", "max_drawdown", "total_return", "win_rate", "trades"}
    if not required.issubset(df.columns):
        return {}
    snapshots: dict[str, str] = {}
    for base_id, group in df.sort_values("cost_bps_per_side").groupby("base_candidate_id", sort=False):
        parts = []
        for row in group.itertuples(index=False):
            parts.append(
                f"{float(row.cost_bps_per_side):g}bps "
                f"cagr={float(row.cagr):.3f} "
                f"dd={float(row.max_drawdown):.3f} "
                f"ret={float(row.total_return):.3f} "
                f"win={float(row.win_rate):.3f} "
                f"trades={float(row.trades):.0f}"
            )
        snapshots[str(base_id)] = "; ".join(parts)
    return snapshots


def _portfolio_validation_for_review(ctx: RunContext, out, review: pd.DataFrame) -> pd.DataFrame:
    trades_path = ctx.stage_dir(2) / "discovery_trades.parquet"
    if not trades_path.exists() or review.empty:
        return pd.DataFrame()
    trades = pd.read_parquet(trades_path)
    id_col = "base_candidate_id" if "base_candidate_id" in review.columns else "candidate_id"
    ids = set(review[id_col].astype(str))
    if "candidate_id" not in trades.columns:
        return pd.DataFrame()
    trades = trades[trades["candidate_id"].astype(str).isin(ids)].copy()
    if trades.empty:
        return pd.DataFrame()
    return_col = "source_return" if "source_return" in trades.columns else "gross_source_return"
    validation = write_portfolio_validation(trades, out, return_col=return_col)
    if validation.empty:
        return pd.DataFrame()
    primary = validation[validation["portfolio_variant"].eq(PRIMARY_VARIANT)].copy()
    if primary.empty:
        return pd.DataFrame()
    rename = {
        "candidate_id": "base_candidate_id",
        "portfolio_events": "portfolio_events",
        "simple_total_return": "portfolio_simple_total_return",
        "return_on_deployed_capital": "portfolio_return_on_deployed_capital",
        "compounded_total_x": "portfolio_compounded_total_x",
        "compounded_cagr": "portfolio_compounded_cagr",
        "max_drawdown": "portfolio_max_drawdown",
        "win_rate": "portfolio_win_rate",
        "active_days": "portfolio_active_days",
        "max_same_day_signals": "portfolio_max_same_day_signals",
        "max_concurrent_positions": "portfolio_max_concurrent_positions",
        "top1_day_log_share": "portfolio_top1_day_log_share",
        "top5_day_log_share": "portfolio_top5_day_log_share",
        "positive_years": "portfolio_positive_years",
        "years_tested": "portfolio_years_tested",
        "worst_year_simple_return": "portfolio_worst_year_simple_return",
        "portfolio_gate_flags": "portfolio_gate_flags",
    }
    keep = [c for c in rename if c in primary.columns]
    out_df = primary[keep].rename(columns=rename)
    out_df["base_candidate_id"] = out_df["base_candidate_id"].astype(str)
    return out_df


def _portfolio_merge_columns() -> list[str]:
    return [
        "portfolio_events",
        "portfolio_simple_total_return",
        "portfolio_return_on_deployed_capital",
        "portfolio_compounded_total_x",
        "portfolio_compounded_cagr",
        "portfolio_max_drawdown",
        "portfolio_win_rate",
        "portfolio_active_days",
        "portfolio_max_same_day_signals",
        "portfolio_max_concurrent_positions",
        "portfolio_top1_day_log_share",
        "portfolio_top5_day_log_share",
        "portfolio_positive_years",
        "portfolio_years_tested",
        "portfolio_worst_year_simple_return",
        "portfolio_gate_flags",
    ]


def _portfolio_gate(
    df: pd.DataFrame,
    *,
    min_trades: float,
    min_active_days: float,
    min_simple_return: float,
    min_return_on_deployed: float,
    max_drawdown: float,
) -> pd.Series:
    required = ["portfolio_events", "portfolio_simple_total_return", "portfolio_return_on_deployed_capital", "portfolio_max_drawdown", "portfolio_active_days"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return pd.Series(False, index=df.index)
    return (
        (pd.to_numeric(df["portfolio_events"], errors="coerce").fillna(0) >= min_trades)
        & (pd.to_numeric(df["portfolio_active_days"], errors="coerce").fillna(0) >= min_active_days)
        & (pd.to_numeric(df["portfolio_simple_total_return"], errors="coerce").fillna(-999) > min_simple_return)
        & (pd.to_numeric(df["portfolio_return_on_deployed_capital"], errors="coerce").fillna(-999) > min_return_on_deployed)
        & (pd.to_numeric(df["portfolio_max_drawdown"], errors="coerce").fillna(-999) >= max_drawdown)
    )


def _promotion_flags(
    row: pd.Series,
    *,
    min_trades: float,
    min_cagr: float,
    max_drawdown: float,
    min_active_days: float,
    min_simple_return: float,
    min_return_on_deployed: float,
) -> str:
    flags: list[str] = []
    trades = _to_float(row.get("trades", 0.0), 0.0)
    cagr = _to_float(row.get("cagr", 0.0), 0.0)
    drawdown = _to_float(row.get("max_drawdown", 0.0), 0.0)
    max_cost = _to_float(row.get("max_profitable_cost_bps_per_side", -1.0), -1.0)
    if trades < min_trades:
        flags.append("low_trade_count")
    if cagr < min_cagr:
        flags.append("below_cagr_gate")
    if drawdown < max_drawdown:
        flags.append("drawdown_breach")
    if max_cost >= 10.0:
        flags.append("cost_robust")
    elif max_cost >= 2.0:
        flags.append("low_cost_survivor")
    else:
        flags.append("fragile_to_costs")
    if cagr >= 0.30 and drawdown >= -0.50:
        flags.append("raw_high_cagr_candidate")
    if trades >= 5000:
        flags.append("high_frequency_capacity_probe")
    portfolio_events = _to_float(row.get("portfolio_events", 0.0), 0.0)
    active_days = _to_float(row.get("portfolio_active_days", 0.0), 0.0)
    simple_return = _to_float(row.get("portfolio_simple_total_return", -999.0), -999.0)
    deployed_return = _to_float(row.get("portfolio_return_on_deployed_capital", -999.0), -999.0)
    portfolio_dd = _to_float(row.get("portfolio_max_drawdown", -999.0), -999.0)
    if portfolio_events <= 0:
        flags.append("portfolio_validation_missing")
    else:
        if portfolio_events < min_trades:
            flags.append("low_portfolio_event_count")
        if active_days < min_active_days:
            flags.append("low_active_days")
        if simple_return <= min_simple_return:
            flags.append("portfolio_simple_return_gate_fail")
        if deployed_return <= min_return_on_deployed:
            flags.append("return_on_deployed_gate_fail")
        if portfolio_dd < max_drawdown:
            flags.append("portfolio_drawdown_breach")
    raw_gate_flags_value = row.get("portfolio_gate_flags", "")
    raw_gate_flags = "" if pd.isna(raw_gate_flags_value) else str(raw_gate_flags_value)
    if raw_gate_flags and raw_gate_flags != "ok":
        flags.extend([x for x in raw_gate_flags.split(",") if x])
    return ",".join(flags)


def _to_float(value: object, default: float) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
