from __future__ import annotations

from pathlib import Path

import pandas as pd

from ar_pipeline.engines.portfolio_validation import RAW_VARIANT, write_portfolio_validation


def run_trade_audit(input_trades: Path, output_dir: Path) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    trades = pd.read_parquet(input_trades)
    required = {"candidate_id", "symbol", "timestamp"}
    missing = required - set(trades.columns)
    if missing:
        raise ValueError(f"trade ledger missing audit columns: {sorted(missing)}")
    dupes = (
        trades.groupby(["candidate_id", "symbol", "timestamp"])
        .size()
        .reset_index(name="count")
        .query("count > 1")
    )
    same_slot = (
        trades.groupby(["candidate_id", "timestamp"])
        .size()
        .reset_index(name="count")
        .query("count > 1")
    )
    by_symbol = trades.groupby(["candidate_id", "symbol"]).size().reset_index(name="trades")
    concentration = by_symbol.sort_values(["candidate_id", "trades"], ascending=[True, False]).groupby("candidate_id").head(5)
    dupes.to_csv(output_dir / "duplicate_trades.csv", index=False)
    same_slot.to_csv(output_dir / "same_timestamp_multiple_trades.csv", index=False)
    concentration.to_csv(output_dir / "symbol_concentration_top5.csv", index=False)
    return_col = _return_column(trades)
    portfolio = pd.DataFrame()
    if return_col:
        portfolio = write_portfolio_validation(trades, output_dir, return_col=return_col, prefix="trade_audit_portfolio_validation")
        raw = portfolio[portfolio["portfolio_variant"].eq(RAW_VARIANT)].copy()
        if not raw.empty:
            raw[
                [
                    "candidate_id",
                    "raw_signal_trades",
                    "active_days",
                    "mean_signals_per_active_day",
                    "max_same_day_signals",
                    "max_concurrent_positions",
                    "raw_full_size_impossible",
                    "top1_day_log_share",
                    "top5_day_log_share",
                    "portfolio_gate_flags",
                ]
            ].to_csv(output_dir / "overlap_and_concentration_audit.csv", index=False)
    summary = {
        "trades": int(len(trades)),
        "duplicate_trade_keys": int(len(dupes)),
        "same_timestamp_multi_trade_keys": int(len(same_slot)),
        "candidates": int(trades["candidate_id"].nunique()),
        "portfolio_validated_candidates": int(portfolio["candidate_id"].nunique()) if not portfolio.empty else 0,
    }
    lines = [
        "# Trade Audit Report",
        "",
        f"Trades: {summary['trades']}",
        f"Candidates: {summary['candidates']}",
        f"Duplicate candidate/symbol/timestamp keys: {summary['duplicate_trade_keys']}",
        f"Candidate timestamps with multiple trades: {summary['same_timestamp_multi_trade_keys']}",
        f"Portfolio validated candidates: {summary['portfolio_validated_candidates']}",
        "",
        "Raw full-size signal compounding is not a deployable portfolio result. See `overlap_and_concentration_audit.csv` and `trade_audit_portfolio_validation.csv`.",
        "",
        "## Symbol Concentration",
        "",
        "```text",
        concentration.to_string(index=False),
        "```",
        "",
    ]
    (output_dir / "trade_audit_report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def _return_column(trades: pd.DataFrame) -> str | None:
    for col in ["quote_return", "source_return", "gross_source_return"]:
        if col in trades.columns:
            return col
    return None
