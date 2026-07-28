from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


RAW_VARIANT = "raw_signal_full_size_not_promotable"
PRIMARY_VARIANT = "one_position_at_a_time"


@dataclass(frozen=True)
class PortfolioPolicy:
    """Capital-use policy for converting a signal ledger into account returns."""

    name: str
    allocation_per_trade: float
    max_concurrent_positions: int
    allow_overlap: bool
    one_trade_per_day: bool = False
    equal_split_same_day: bool = False


DEFAULT_POLICIES = [
    PortfolioPolicy(RAW_VARIANT, 1.0, 999_999, True),
    PortfolioPolicy(PRIMARY_VARIANT, 1.0, 1, False),
    PortfolioPolicy("one_trade_per_day_first", 1.0, 1, False, one_trade_per_day=True),
    PortfolioPolicy("equal_split_same_day_100pct", 1.0, 999_999, True, equal_split_same_day=True),
]


def validate_trade_ledger(
    trades: pd.DataFrame,
    *,
    return_col: str = "source_return",
    candidate_col: str = "candidate_id",
    allocation_per_trade: float = 1.0,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return per-candidate portfolio metrics, yearly metrics, and selected events.

    The raw signal ledger is preserved only as a diagnostic. Promotion should use
    `one_position_at_a_time` or an explicit user-defined capital policy.
    """

    required = {candidate_col, "entry_ts", "exit_ts", return_col}
    if "entry_ts" not in trades.columns and "timestamp" in trades.columns:
        trades = trades.copy()
        trades["entry_ts"] = trades["timestamp"]
    missing = required - set(trades.columns)
    if missing:
        raise ValueError(f"trade ledger missing portfolio validation columns: {sorted(missing)}")

    work = trades.copy()
    work["entry_ts"] = pd.to_datetime(work["entry_ts"], utc=True, format="mixed")
    work["exit_ts"] = pd.to_datetime(work["exit_ts"], utc=True, format="mixed")
    work[return_col] = pd.to_numeric(work[return_col], errors="coerce")
    work = work.dropna(subset=[candidate_col, "entry_ts", "exit_ts", return_col]).copy()
    work = work[np.isfinite(work[return_col].astype(float))].copy()
    if work.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    work["_ny_date"] = work["entry_ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")

    policies = [
        PortfolioPolicy(p.name, allocation_per_trade, p.max_concurrent_positions, p.allow_overlap, p.one_trade_per_day, p.equal_split_same_day)
        for p in DEFAULT_POLICIES
    ]
    metric_rows: list[dict[str, Any]] = []
    year_rows: list[dict[str, Any]] = []
    event_rows: list[pd.DataFrame] = []
    for candidate_id, group in work.groupby(candidate_col, sort=False):
        group = group.sort_values(["entry_ts", "exit_ts"]).reset_index(drop=True)
        overlap_stats = _overlap_stats(group)
        concentration = _raw_day_concentration(group, return_col=return_col)
        for policy in policies:
            events = _events_for_policy(group, policy, return_col=return_col, candidate_col=candidate_col)
            metrics = _portfolio_metrics(events)
            impossible = (
                policy.name == RAW_VARIANT
                and allocation_per_trade >= 1.0
                and (overlap_stats["max_same_day_signals"] > 1 or overlap_stats["max_concurrent_positions"] > 1)
            )
            row = {
                "candidate_id": str(candidate_id),
                "portfolio_variant": policy.name,
                "capital_model": _capital_model_label(policy),
                "allocation_per_trade": allocation_per_trade,
                "raw_signal_trades": int(len(group)),
                "active_days": overlap_stats["active_days"],
                "mean_signals_per_active_day": overlap_stats["mean_signals_per_active_day"],
                "median_signals_per_active_day": overlap_stats["median_signals_per_active_day"],
                "max_same_day_signals": overlap_stats["max_same_day_signals"],
                "max_concurrent_positions": overlap_stats["max_concurrent_positions"],
                "overlap_violation": bool(allocation_per_trade >= 1.0 and overlap_stats["max_concurrent_positions"] > 1),
                "raw_full_size_impossible": bool(impossible),
                **concentration,
                **metrics,
            }
            row["portfolio_gate_flags"] = _gate_flags(row)
            metric_rows.append(row)
            if not events.empty:
                ev = events.copy()
                ev["candidate_id"] = str(candidate_id)
                ev["portfolio_variant"] = policy.name
                event_rows.append(ev)
                yearly = _yearly_metrics(ev)
                for year, vals in yearly.items():
                    year_rows.append({"candidate_id": str(candidate_id), "portfolio_variant": policy.name, "year": year, **vals})
    metrics_df = pd.DataFrame(metric_rows)
    years_df = pd.DataFrame(year_rows)
    events_df = pd.concat(event_rows, ignore_index=True) if event_rows else pd.DataFrame()
    return metrics_df, years_df, events_df


def write_portfolio_validation(
    trades: pd.DataFrame,
    output_dir: Path,
    *,
    return_col: str = "source_return",
    prefix: str = "portfolio_validation",
) -> pd.DataFrame:
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics, years, events = validate_trade_ledger(trades, return_col=return_col)
    metrics.to_csv(output_dir / f"{prefix}.csv", index=False)
    years.to_csv(output_dir / f"{prefix}_years.csv", index=False)
    if not events.empty:
        events.to_parquet(output_dir / f"{prefix}_events.parquet", index=False)
    lines = [
        "# Portfolio Validation",
        "",
        f"Return column: `{return_col}`",
        "",
        "Raw signal compounding is diagnostic only. Promotion must use a capital-aware portfolio variant.",
        "",
    ]
    if not metrics.empty:
        primary = metrics[metrics["portfolio_variant"].eq(PRIMARY_VARIANT)].sort_values(
            ["simple_total_return", "return_on_deployed_capital"], ascending=False
        )
        lines.extend(["## Primary Variant: one_position_at_a_time", "", "```text", primary.head(50).to_string(index=False), "```", ""])
    (output_dir / f"{prefix}.md").write_text("\n".join(lines), encoding="utf-8")
    return metrics


def _events_for_policy(group: pd.DataFrame, policy: PortfolioPolicy, *, return_col: str, candidate_col: str) -> pd.DataFrame:
    if policy.name == RAW_VARIANT:
        out = group[["entry_ts", "exit_ts", return_col]].copy()
        out["event_return"] = out[return_col].astype(float) * policy.allocation_per_trade
        out["capital_deployed"] = policy.allocation_per_trade
        out["source_signal_count"] = 1
        return out.drop(columns=[return_col])
    if policy.one_trade_per_day:
        out = group.sort_values(["entry_ts", "exit_ts"]).groupby("_ny_date", sort=True).head(1)
        ev = out[["entry_ts", "exit_ts", return_col]].copy()
        ev["event_return"] = ev[return_col].astype(float) * policy.allocation_per_trade
        ev["capital_deployed"] = policy.allocation_per_trade
        ev["source_signal_count"] = 1
        return ev.drop(columns=[return_col])
    if policy.equal_split_same_day:
        rows: list[dict[str, Any]] = []
        for _, day in group.sort_values(["entry_ts", "exit_ts"]).groupby("_ny_date", sort=True):
            n = max(len(day), 1)
            alloc = policy.allocation_per_trade / n
            rows.append(
                {
                    "entry_ts": day["entry_ts"].min(),
                    "exit_ts": day["exit_ts"].max(),
                    "event_return": float((day[return_col].astype(float) * alloc).sum()),
                    "capital_deployed": float(policy.allocation_per_trade),
                    "source_signal_count": int(n),
                }
            )
        return pd.DataFrame(rows)
    if policy.name == PRIMARY_VARIANT:
        rows = []
        next_free = pd.Timestamp.min.tz_localize("UTC")
        for row in group.sort_values(["entry_ts", "exit_ts"]).itertuples(index=False):
            if row.entry_ts >= next_free:
                ret = float(getattr(row, return_col))
                rows.append(
                    {
                        "entry_ts": row.entry_ts,
                        "exit_ts": row.exit_ts,
                        "event_return": ret * policy.allocation_per_trade,
                        "capital_deployed": policy.allocation_per_trade,
                        "source_signal_count": 1,
                    }
                )
                next_free = row.exit_ts
        return pd.DataFrame(rows)
    raise ValueError(f"Unsupported portfolio policy: {policy.name}")


def _portfolio_metrics(events: pd.DataFrame) -> dict[str, Any]:
    if events.empty:
        return {
            "portfolio_events": 0,
            "simple_total_return": 0.0,
            "capital_deployed_turnover": 0.0,
            "return_on_deployed_capital": 0.0,
            "compounded_total_x": 1.0,
            "compounded_cagr": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "avg_event_return": 0.0,
            "median_event_return": 0.0,
            "positive_years": 0,
            "years_tested": 0,
            "worst_year_simple_return": 0.0,
        }
    returns = events["event_return"].astype(float).to_numpy()
    capital = events["capital_deployed"].astype(float).to_numpy()
    equity = np.cumprod(np.clip(1.0 + returns, 1e-12, None))
    peak = np.maximum.accumulate(equity)
    dd = equity / np.maximum(peak, 1e-12) - 1.0
    start = pd.to_datetime(events["entry_ts"], utc=True).min()
    end = pd.to_datetime(events["exit_ts"], utc=True).max()
    days = max((end - start).total_seconds() / (24 * 60 * 60), 1.0)
    years = days / 365.25
    total_x = float(equity[-1])
    yearly = _yearly_metrics(events)
    yearly_returns = [float(v["simple_total_return"]) for v in yearly.values()]
    capital_turnover = float(np.abs(capital).sum())
    simple_total = float(returns.sum())
    return {
        "portfolio_events": int(len(events)),
        "simple_total_return": simple_total,
        "capital_deployed_turnover": capital_turnover,
        "return_on_deployed_capital": float(simple_total / capital_turnover) if capital_turnover else 0.0,
        "compounded_total_x": total_x,
        "compounded_cagr": _safe_cagr(total_x, years),
        "max_drawdown": float(dd.min()),
        "win_rate": float((returns > 0).mean()),
        "avg_event_return": float(np.mean(returns)),
        "median_event_return": float(np.median(returns)),
        "positive_years": int(sum(x > 0 for x in yearly_returns)),
        "years_tested": int(len(yearly_returns)),
        "worst_year_simple_return": float(min(yearly_returns)) if yearly_returns else 0.0,
    }


def _yearly_metrics(events: pd.DataFrame) -> dict[int, dict[str, Any]]:
    if events.empty:
        return {}
    work = events.copy()
    work["year"] = pd.to_datetime(work["entry_ts"], utc=True).dt.year
    out: dict[int, dict[str, Any]] = {}
    for year, group in work.groupby("year", sort=True):
        returns = group["event_return"].astype(float)
        out[int(year)] = {
            "events": int(len(group)),
            "simple_total_return": float(returns.sum()),
            "compounded_total_return": float((1.0 + returns).prod() - 1.0),
            "return_on_deployed_capital": float(returns.sum() / group["capital_deployed"].abs().sum()) if group["capital_deployed"].abs().sum() else 0.0,
        }
    return out


def _safe_cagr(total_x: float, years: float) -> float:
    if total_x <= 0:
        return -1.0
    log_cagr = np.log(total_x) / max(years, 1e-9)
    if log_cagr > 80:
        return float("inf")
    if log_cagr < -80:
        return -1.0
    return float(np.expm1(log_cagr))


def _overlap_stats(group: pd.DataFrame) -> dict[str, Any]:
    active_days = group["_ny_date"].nunique()
    day_counts = group.groupby("_ny_date").size()
    max_concurrent = _max_concurrent(group)
    return {
        "active_days": int(active_days),
        "mean_signals_per_active_day": float(day_counts.mean()) if len(day_counts) else 0.0,
        "median_signals_per_active_day": float(day_counts.median()) if len(day_counts) else 0.0,
        "max_same_day_signals": int(day_counts.max()) if len(day_counts) else 0,
        "max_concurrent_positions": int(max_concurrent),
    }


def _max_concurrent(group: pd.DataFrame) -> int:
    events: list[tuple[pd.Timestamp, int]] = []
    for row in group.itertuples(index=False):
        events.append((row.entry_ts, 1))
        events.append((row.exit_ts, -1))
    # Exits sort before entries at the same timestamp so back-to-back trades are not overlap.
    active = 0
    max_active = 0
    for _, delta in sorted(events, key=lambda x: (x[0], x[1])):
        active += delta
        max_active = max(max_active, active)
    return max_active


def _raw_day_concentration(group: pd.DataFrame, *, return_col: str) -> dict[str, Any]:
    day_returns = group.groupby("_ny_date")[return_col].apply(lambda s: float(np.log1p(np.clip(s.astype(float), -0.999999, None)).sum()))
    total = float(day_returns.sum()) if len(day_returns) else 0.0
    if abs(total) < 1e-12:
        return {"top1_day_log_share": 0.0, "top3_day_log_share": 0.0, "top5_day_log_share": 0.0}
    ranked = day_returns.sort_values(ascending=False)
    return {
        "top1_day_log_share": float(ranked.head(1).sum() / total),
        "top3_day_log_share": float(ranked.head(3).sum() / total),
        "top5_day_log_share": float(ranked.head(5).sum() / total),
    }


def _capital_model_label(policy: PortfolioPolicy) -> str:
    if policy.name == RAW_VARIANT:
        return "diagnostic_raw_full_size_each_signal"
    if policy.name == PRIMARY_VARIANT:
        return "cash_locked_skip_while_flat"
    if policy.one_trade_per_day:
        return "cash_locked_first_signal_each_day"
    if policy.equal_split_same_day:
        return "100pct_capital_split_across_same_day_signals"
    return policy.name


def _gate_flags(row: dict[str, Any]) -> str:
    flags: list[str] = []
    if row.get("raw_full_size_impossible"):
        flags.append("raw_full_size_impossible")
    if row.get("overlap_violation"):
        flags.append("overlap_violation_for_100pct_deployment")
    if float(row.get("top1_day_log_share", 0.0) or 0.0) > 0.20:
        flags.append("top1_day_concentrated")
    if float(row.get("top5_day_log_share", 0.0) or 0.0) > 0.50:
        flags.append("top5_day_concentrated")
    if int(row.get("active_days", 0) or 0) < 100:
        flags.append("sparse_active_days")
    if float(row.get("simple_total_return", 0.0) or 0.0) <= 0:
        flags.append("nonpositive_simple_return")
    return ",".join(flags) if flags else "ok"
