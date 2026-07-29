from __future__ import annotations

import numpy as np
import pandas as pd


CUTOFF = pd.Timestamp("2026-04-30")
HOLDOUT = pd.Timestamp("2026-05-01")


def validate_cutoff(frame: pd.DataFrame, column: str = "date") -> None:
    dates = pd.to_datetime(frame[column])
    if len(dates) and dates.max() > CUTOFF:
        raise RuntimeError("sealed holdout row loaded")


def net_return(entry: float, exit_: float, cost_bps_per_side: float) -> float:
    cost = cost_bps_per_side / 10_000.0
    return (exit_ * (1-cost)) / (entry * (1+cost)) - 1


def max_drawdown_and_recovery(daily: pd.DataFrame) -> tuple[float, int, bool]:
    equity = 1.0 + daily["net_pnl"].cumsum()
    peaks = equity.cummax()
    dd = 1 - equity / peaks
    trough_index = int(dd.to_numpy().argmax())
    peak_before = float(peaks.iloc[trough_index])
    later = np.flatnonzero(equity.iloc[trough_index:].to_numpy() >= peak_before)
    if len(later):
        end = trough_index + int(later[0])
        days = int((daily.iloc[end]["date"] - daily.iloc[trough_index]["date"]).days)
        return float(dd.max()), days, False
    days = int((daily.iloc[-1]["date"] - daily.iloc[trough_index]["date"]).days)
    return float(dd.max()), days, True


def source_signal(previous_close: float, completed_0959_close: float) -> bool:
    return completed_0959_close / previous_close - 1 > 0
