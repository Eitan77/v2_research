from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd


CUTOFF = pd.Timestamp("2026-04-30")
HOLDOUT_START = pd.Timestamp("2026-05-01")


def _conditions(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {str(x) for x in value}
    return set()


def select_official_open(records: list[dict]) -> tuple[dict | None, str]:
    valid = []
    opening = []
    for record in records:
        try:
            price = float(record["p"])
            size = float(record["s"])
            exchange = str(record["x"])
            timestamp = str(record["t"])
        except (KeyError, TypeError, ValueError):
            continue
        if not np.isfinite(price) or price <= 0 or not np.isfinite(size) or size <= 0:
            continue
        normalized = {
            "price": price,
            "size": size,
            "exchange": exchange,
            "timestamp": timestamp,
        }
        conditions = _conditions(record.get("c"))
        if "Q" in conditions:
            valid.append(normalized)
        if "O" in conditions:
            opening.append(normalized)
    if not valid:
        return None, "missing_official_q"

    matched = []
    for candidate in valid:
        if any(
            item["exchange"] == candidate["exchange"]
            and np.isclose(item["price"], candidate["price"], rtol=0, atol=1e-9)
            and item["size"] == candidate["size"]
            for item in opening
        ):
            matched.append(candidate)
    if not matched:
        return None, "official_q_without_matching_open"

    unique = {
        (
            item["exchange"],
            item["price"],
            item["size"],
            item["timestamp"],
        ): item
        for item in matched
    }
    candidates = list(unique.values())
    maximum_size = max(item["size"] for item in candidates)
    leaders = [item for item in candidates if item["size"] == maximum_size]
    leader_prices = {item["price"] for item in leaders}
    leader_exchanges = {item["exchange"] for item in leaders}
    if len(leader_prices) != 1 or len(leader_exchanges) != 1:
        return None, "ambiguous_maximum_official_open"
    return leaders[0], "selected"


def is_probable_split_ratio(
    current_price: float, prior_price: float, relative_tolerance: float = 0.03
) -> bool:
    if not np.isfinite(current_price) or not np.isfinite(prior_price):
        return False
    if current_price <= 0 or prior_price <= 0:
        return False
    ratio = current_price / prior_price
    common = [1.0 / n for n in range(2, 26)] + [float(n) for n in range(2, 26)]
    return any(abs(ratio - factor) / factor <= relative_tolerance for factor in common)


def marketable_long_return(
    entry: float, exit_price: float, cost_bps_per_side: float
) -> float:
    return exit_price / entry - 1.0 - 2.0 * cost_bps_per_side / 10_000.0


def protected_short_return(
    entry: float,
    planned_exit: float,
    path_highs: Iterable[float],
    stop_fraction: float,
    cost_bps_per_side: float,
    adverse_stop_slippage_bps: float = 10.0,
) -> tuple[float, bool, float]:
    stop_price = entry * (1.0 + stop_fraction)
    stopped = any(float(high) >= stop_price for high in path_highs)
    exit_price = (
        stop_price * (1.0 + adverse_stop_slippage_bps / 10_000.0)
        if stopped
        else planned_exit
    )
    pnl = (entry - exit_price) / entry - 2.0 * cost_bps_per_side / 10_000.0
    return float(pnl), stopped, float(exit_price)


def protected_long_exit(
    entry: float,
    planned_exit: float,
    path_opens_and_lows: Iterable[tuple[float, float]],
    stop_fraction: float | None,
    adverse_stop_slippage_bps: float = 10.0,
) -> tuple[float, bool]:
    """Return a conservative long exit price from an observed trade-bar path."""
    if stop_fraction is None:
        return float(planned_exit), False
    stop_price = entry * (1.0 - stop_fraction)
    for open_price, low_price in path_opens_and_lows:
        open_price = float(open_price)
        low_price = float(low_price)
        if open_price <= stop_price:
            fill = open_price * (1.0 - adverse_stop_slippage_bps / 10_000.0)
            return float(fill), True
        if low_price <= stop_price:
            fill = stop_price * (1.0 - adverse_stop_slippage_bps / 10_000.0)
            return float(fill), True
    return float(planned_exit), False


def allocate_daily(
    long_returns: list[float],
    short_returns: list[float],
    portfolio: str,
) -> float:
    if portfolio == "long_only":
        return float(np.mean(long_returns)) if long_returns else 0.0
    if portfolio == "short_only":
        return float(np.mean(short_returns)) if short_returns else 0.0
    if portfolio == "balanced":
        long_pnl = float(np.mean(long_returns)) * 0.5 if long_returns else 0.0
        short_pnl = float(np.mean(short_returns)) * 0.5 if short_returns else 0.0
        return long_pnl + short_pnl
    raise ValueError(f"Unknown portfolio {portfolio}")


def max_drawdown_and_recovery(
    daily: pd.DataFrame, pnl_column: str = "net_pnl"
) -> tuple[float, int | None, bool]:
    frame = daily.sort_values("date").reset_index(drop=True).copy()
    if frame.empty:
        return 0.0, None, True
    equity = 1.0 + frame[pnl_column].cumsum()
    peak = equity.cummax()
    drawdown = (peak - equity) / peak
    maximum_position = int(drawdown.to_numpy().argmax())
    maximum_drawdown = float(drawdown.iloc[maximum_position])
    peak_value = float(peak.iloc[maximum_position])
    recovered = np.flatnonzero(
        (np.arange(len(frame)) > maximum_position)
        & (equity.to_numpy() >= peak_value)
    )
    if len(recovered) == 0:
        return maximum_drawdown, None, True
    recovery_index = int(recovered[0])
    days = (
        pd.Timestamp(frame.loc[recovery_index, "date"])
        - pd.Timestamp(frame.loc[maximum_position, "date"])
    ).days
    return maximum_drawdown, int(days), False
