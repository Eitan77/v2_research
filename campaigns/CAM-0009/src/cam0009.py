from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd


def completed_window(
    frame: pd.DataFrame, start_minute: int, length: int
) -> pd.DataFrame | None:
    expected = list(range(start_minute, start_minute + length))
    selected = frame[
        frame["minute_number"].between(
            start_minute, start_minute + length - 1
        )
    ].sort_values("minute_number")
    if selected["minute_number"].tolist() != expected:
        return None
    return selected


def shifted_rolling_median(
    values: pd.Series, window: int, minimum: int
) -> pd.Series:
    return values.shift(1).rolling(window, min_periods=minimum).median()


def select_lagging_peers(
    frame: pd.DataFrame,
    leader_symbol: str,
    leader_residual: float,
    maximum_signed_ratio: float,
    maximum_peers: int,
) -> pd.DataFrame:
    direction = 1 if leader_residual > 0 else -1
    candidates = frame[frame["symbol"].ne(leader_symbol)].copy()
    candidates["signed_peer_residual"] = (
        direction * candidates["residual_return"]
    )
    candidates = candidates[
        candidates["signed_peer_residual"].le(
            abs(leader_residual) * maximum_signed_ratio
        )
    ]
    return candidates.sort_values(
        ["prior20_median_dollar_volume", "symbol"],
        ascending=[False, True],
    ).head(maximum_peers)


def protected_short_return(
    entry: float,
    planned_exit: float,
    path_highs: list[float],
    stop_fraction: float,
    cost_bps_per_side: float,
    adverse_stop_slippage_bps: float,
) -> tuple[float, bool, float]:
    stop = entry * (1 + stop_fraction)
    stopped = any(float(value) >= stop for value in path_highs)
    effective_exit = (
        stop * (1 + adverse_stop_slippage_bps / 10_000)
        if stopped
        else planned_exit
    )
    net_return = (
        (entry - effective_exit) / entry
        - 2 * cost_bps_per_side / 10_000
    )
    return float(net_return), bool(stopped), float(effective_exit)


def protected_long_return(
    entry: float,
    planned_exit: float,
    path_lows: list[float],
    stop_fraction: float | None,
    cost_bps_per_side: float,
    adverse_stop_slippage_bps: float,
) -> tuple[float, bool, float]:
    stopped = False
    effective_exit = planned_exit
    if stop_fraction is not None:
        stop = entry * (1 - stop_fraction)
        stopped = any(float(value) <= stop for value in path_lows)
        if stopped:
            effective_exit = stop * (
                1 - adverse_stop_slippage_bps / 10_000
            )
    net_return = (
        effective_exit / entry
        - 1
        - 2 * cost_bps_per_side / 10_000
    )
    return float(net_return), bool(stopped), float(effective_exit)


def allocate_intraday(
    candidates: pd.DataFrame,
    position_cap: float,
    symbol_cap: float,
    gross_cap: float = 1.0,
) -> pd.DataFrame:
    result = candidates.sort_values(
        ["entry_timestamp", "symbol", "leader_symbol"]
    ).copy()
    result["position_fraction"] = 0.0
    active: list[tuple[pd.Timestamp, str, float]] = []
    for timestamp, indices in result.groupby(
        "entry_timestamp", sort=True
    ).groups.items():
        entry = pd.Timestamp(timestamp)
        active = [item for item in active if item[0] > entry]
        available = max(0.0, gross_cap - sum(item[2] for item in active))
        if available <= 1e-12:
            continue
        cohort_size = min(position_cap, available / len(indices))
        symbol_gross: defaultdict[str, float] = defaultdict(float)
        for _, symbol, size in active:
            symbol_gross[symbol] += size
        for index in indices:
            symbol = str(result.loc[index, "symbol"])
            size = min(
                cohort_size,
                max(0.0, symbol_cap - symbol_gross[symbol]),
            )
            if size <= 1e-12:
                continue
            result.loc[index, "position_fraction"] = size
            active.append(
                (
                    pd.Timestamp(result.loc[index, "exit_timestamp"]),
                    symbol,
                    float(size),
                )
            )
            symbol_gross[symbol] += float(size)
    return result


def max_drawdown_and_recovery(
    daily: pd.DataFrame,
) -> tuple[float, int | None, bool]:
    frame = daily.sort_values("date").reset_index(drop=True)
    equity = 1.0 + frame["net_pnl"].cumsum()
    peak = equity.cummax().clip(lower=1.0)
    drawdown = (peak - equity) / peak
    location = int(drawdown.to_numpy().argmax())
    maximum = float(drawdown.iloc[location])
    if maximum <= 1e-15:
        return 0.0, 0, False
    peak_value = float(peak.iloc[location])
    recovered = np.flatnonzero(
        (np.arange(len(frame)) > location)
        & (equity.to_numpy() >= peak_value)
    )
    if len(recovered) == 0:
        return maximum, None, True
    recovery = int(recovered[0])
    days = int(
        (
            pd.Timestamp(frame.loc[recovery, "date"])
            - pd.Timestamp(frame.loc[location, "date"])
        ).days
    )
    return maximum, days, False
