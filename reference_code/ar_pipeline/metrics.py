from __future__ import annotations

import numpy as np
import pandas as pd


def equity_metrics(returns: pd.Series | np.ndarray, periods_per_year: float = 252.0) -> dict[str, float]:
    arr = np.asarray(returns, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {
            "trades": 0.0,
            "total_return": 0.0,
            "cagr": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "worst_return": 0.0,
        }
    eq = np.cumprod(1.0 + arr)
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1.0
    years = max(arr.size / periods_per_year, 1.0 / periods_per_year)
    total = float(eq[-1] - 1.0)
    cagr = float(eq[-1] ** (1.0 / years) - 1.0) if eq[-1] > 0 else -1.0
    return {
        "trades": float(arr.size),
        "total_return": total,
        "cagr": cagr,
        "max_drawdown": float(dd.min()),
        "win_rate": float((arr > 0).mean()),
        "avg_return": float(arr.mean()),
        "worst_return": float(arr.min()),
    }
