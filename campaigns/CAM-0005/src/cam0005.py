from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd


CUTOFF = pd.Timestamp("2026-04-30")
HOLDOUT_START = pd.Timestamp("2026-05-01")


def validate_cutoff(frame: pd.DataFrame, column: str = "session") -> None:
    dates = pd.to_datetime(frame[column]).dt.tz_localize(None)
    if dates.max() > CUTOFF or dates.ge(HOLDOUT_START).any():
        raise RuntimeError("sealed holdout row loaded")


def stable_frame_hash(frame: pd.DataFrame, sort_columns: list[str]) -> str:
    ordered = frame.sort_values(sort_columns).reset_index(drop=True)
    values = pd.util.hash_pandas_object(ordered, index=False).values.tobytes()
    return hashlib.sha256(values).hexdigest()


def marketable_long_return(
    entry: float, exit_: float, cost_bps_per_side: float
) -> float:
    return exit_ / entry - 1.0 - 2.0 * cost_bps_per_side / 10_000.0


def direction_product(
    underlying_return: float, mapping: str, bull: str, inverse: str
) -> str:
    continuation = bull if underlying_return > 0 else inverse
    if mapping == "continuation":
        return continuation
    if mapping == "reversal":
        return inverse if continuation == bull else bull
    raise ValueError(f"unknown mapping {mapping}")


def allocate_pair_pnl(pair_returns: list[float]) -> float:
    if not pair_returns:
        return 0.0
    return float(np.mean(pair_returns))


def rolling_prior_quantile(
    series: pd.Series, quantile: float, window: int = 60, minimum: int = 40
) -> pd.Series:
    return series.rolling(window, min_periods=minimum).quantile(quantile).shift(1)


def max_drawdown_and_recovery(
    daily: pd.DataFrame, pnl_column: str = "net_pnl"
) -> tuple[float, int | None, bool]:
    frame = daily.sort_values("date").copy()
    equity = 1.0 + frame[pnl_column].cumsum()
    peak = equity.cummax()
    drawdown = (peak - equity) / peak
    max_index = int(drawdown.values.argmax())
    max_drawdown = float(drawdown.iloc[max_index])
    peak_value = float(peak.iloc[max_index])
    recovery_rows = frame.index[
        (frame.index > frame.index[max_index]) & (equity >= peak_value)
    ]
    if len(recovery_rows) == 0:
        return max_drawdown, None, True
    recovery_index = recovery_rows[0]
    days = (
        pd.Timestamp(frame.loc[recovery_index, "date"])
        - pd.Timestamp(frame.iloc[max_index]["date"])
    ).days
    return max_drawdown, int(days), False
