from __future__ import annotations

import pandas as pd


CUTOFF = pd.Timestamp("2026-04-30")
HOLDOUT_START = pd.Timestamp("2026-05-01")


def validate_cutoff(frame: pd.DataFrame, date_column: str = "date") -> None:
    dates = pd.to_datetime(frame[date_column])
    if dates.max() > CUTOFF or int((dates >= HOLDOUT_START).sum()):
        raise RuntimeError("sealed holdout row detected")


def choose_nonoverlapping_clusters(
    events: pd.DataFrame, holding_minutes: int
) -> pd.DataFrame:
    """Trade all same-minute events equally, then admit no new cluster until exit."""
    if events.empty:
        return events.copy()
    events = events.sort_values(["event_ts", "symbol"]).copy()
    selected = []
    next_free = None
    for timestamp, group in events.groupby("event_ts", sort=True):
        timestamp = pd.Timestamp(timestamp)
        if next_free is not None and timestamp < next_free:
            continue
        group = group.copy()
        group["weight"] = 1.0 / len(group)
        group["cluster_size"] = len(group)
        selected.append(group)
        next_free = timestamp + pd.Timedelta(minutes=holding_minutes + 1)
    return pd.concat(selected, ignore_index=True) if selected else events.iloc[0:0].copy()


def max_drawdown_and_recovery(daily: pd.DataFrame) -> tuple[float, int, bool]:
    equity = 1.0 + daily["net_pnl"].cumsum()
    peak = equity.cummax()
    drawdown = (peak - equity) / peak
    max_dd = float(drawdown.max()) if len(drawdown) else 0.0
    longest = 0
    start = None
    for date, value in zip(pd.to_datetime(daily["date"]), drawdown):
        if value > 1e-12 and start is None:
            start = date
        elif value <= 1e-12 and start is not None:
            longest = max(longest, int((date - start).days))
            start = None
    unresolved = start is not None
    if unresolved:
        longest = max(longest, int((pd.to_datetime(daily["date"]).iloc[-1] - start).days))
    return max_dd, longest, unresolved


def event_net_return(entry: float, exit_: float, cost_bps_per_side: float) -> float:
    if entry <= 0 or exit_ <= 0:
        raise ValueError("prices must be positive")
    return exit_ / entry - 1.0 - 2.0 * cost_bps_per_side / 10_000.0


def source_trigger(completed_return_60m: float, causal_prior_normal: float) -> bool:
    if causal_prior_normal <= 0:
        return False
    return completed_return_60m <= -0.04 and -completed_return_60m >= 8.0 * causal_prior_normal
