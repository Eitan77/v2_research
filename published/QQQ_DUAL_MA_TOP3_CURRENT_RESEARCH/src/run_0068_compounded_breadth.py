from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0600" / "src"))

from run_0033_exit_overlays import base_context
from run_0067_last_year_breadth import extension, signals

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0068"
COST = 9.740340418 / 10000.0
START = pd.Timestamp("2025-08-15")
END = pd.Timestamp("2026-08-14")
RESERVE = 0.005


def solve_equal_target(cash: float, current: np.ndarray, selected: np.ndarray, nav: float) -> tuple[float, float]:
    selected_mask = np.zeros(len(current), dtype=bool)
    selected_mask[selected] = True
    reserve_cash = RESERVE * nav

    def ending_cash(target_value: float) -> float:
        wanted = np.where(selected_mask, target_value, 0.0)
        delta = wanted - current
        return float(cash - delta.sum() - COST * np.abs(delta).sum())

    lo, hi = 0.0, nav / max(1, len(selected))
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if ending_cash(mid) >= reserve_cash:
            lo = mid
        else:
            hi = mid
    return lo, ending_cash(lo)


def simulate(dates, asset_returns, score, mask, top_n):
    signal_indices = signals(dates)
    execution_targets: dict[int, tuple[int, ...]] = {}
    for signal_i in signal_indices:
        execution_i = int(signal_i) + 1
        if execution_i >= len(dates):
            continue
        candidates = np.flatnonzero(mask[signal_i] & np.isfinite(score[signal_i]))
        chosen = candidates[np.argsort(score[signal_i, candidates], kind="stable")[-min(top_n, len(candidates)):]] if len(candidates) else np.array([], dtype=int)
        execution_targets[execution_i] = tuple(sorted(int(x) for x in chosen))

    current = np.zeros(asset_returns.shape[1], dtype=float)
    cash = 1.0
    active: tuple[int, ...] = tuple()
    rows = []
    minimum_cash = cash
    maximum_gross = 0.0
    rebalance_sessions = 0
    for i, day in enumerate(dates):
        if i in execution_targets:
            target = execution_targets[i]
            if target != active:
                nav = cash + current.sum()
                selected = np.asarray(target, dtype=int)
                if len(selected):
                    target_value, cash = solve_equal_target(cash, current, selected, nav)
                    wanted = np.zeros_like(current)
                    wanted[selected] = target_value
                    current = wanted
                else:
                    sale_cost = COST * current.sum()
                    cash += current.sum() - sale_cost
                    current[:] = 0.0
                active = target
                rebalance_sessions += 1
        gross = current.sum()
        nav_open = cash + gross
        if cash < -1e-12 or (nav_open > 0 and gross / nav_open > 1.0 + 1e-12):
            raise RuntimeError(f"cash/exposure failure on {day}: cash={cash}, gross={gross}, nav={nav_open}")
        r = np.nan_to_num(asset_returns[i], nan=0.0)
        current *= 1.0 + r
        equity = cash + current.sum()
        gross_close = current.sum()
        minimum_cash = min(minimum_cash, cash)
        maximum_gross = max(maximum_gross, gross_close / equity if equity > 0 else np.inf)
        rows.append({"date": pd.Timestamp(day), "equity": equity, "cash": cash,
                     "gross_value": gross_close, "rebalanced": i in execution_targets and execution_targets[i] == active})

    daily = pd.DataFrame(rows).set_index("date")
    before = daily.loc[daily.index < START, "equity"]
    window = daily.loc[(daily.index >= START) & (daily.index <= END), "equity"]
    start_equity = float(before.iloc[-1]) if len(before) else 1.0
    path = pd.concat([pd.Series([start_equity], index=[START - pd.Timedelta(nanoseconds=1)]), window])
    peaks = path.cummax()
    drawdown = path / peaks - 1.0
    trough = drawdown.idxmin()
    peak = path.loc[:trough].idxmax()
    return daily, {
        "top_n": top_n,
        "starting_equity": start_equity,
        "ending_equity": float(window.iloc[-1]),
        "compounded_return": float(window.iloc[-1] / start_equity - 1.0),
        "maximum_drawdown": float(-drawdown.min()),
        "drawdown_peak": str(pd.Timestamp(peak).date()),
        "drawdown_trough": str(pd.Timestamp(trough).date()),
        "rebalance_sessions_full_history": rebalance_sessions,
        "minimum_cash": float(minimum_cash),
        "maximum_gross_to_equity": float(maximum_gross),
    }


def panel():
    p, hist_score, hist_mask, _, _, _, _ = base_context()
    ext_dates, ext_open, ext_close, ext_dv, ext_member, membership_max = extension(p)
    dates = pd.DatetimeIndex(list(pd.DatetimeIndex(p.dates)) + list(ext_dates))
    op = np.vstack([p.adj_open, ext_open])
    cl = np.vstack([p.adj_close, ext_close])
    returns = np.full_like(op, np.nan)
    returns[:len(p.dates)] = p.open_to_next_open_return
    returns[len(p.dates) - 1] = np.divide(ext_open[0], p.adj_open[-1], out=np.zeros(op.shape[1]),
                                          where=np.isfinite(ext_open[0]) & np.isfinite(p.adj_open[-1]) & (p.adj_open[-1] > 0)) - 1.0
    for j in range(len(ext_dates)):
        i = len(p.dates) + j
        if j + 1 < len(ext_dates):
            returns[i] = np.divide(ext_open[j + 1], ext_open[j], out=np.zeros(op.shape[1]),
                                    where=np.isfinite(ext_open[j + 1]) & np.isfinite(ext_open[j]) & (ext_open[j] > 0)) - 1.0
        else:
            returns[i] = np.divide(ext_close[j], ext_open[j], out=np.zeros(op.shape[1]),
                                    where=np.isfinite(ext_close[j]) & np.isfinite(ext_open[j]) & (ext_open[j] > 0)) - 1.0
    close_df = pd.DataFrame(cl, index=dates)
    dv_df = pd.DataFrame(np.vstack([p.raw_close * p.volume, ext_dv]), index=dates)
    sma50 = close_df.rolling(50, min_periods=50).mean().to_numpy()
    sma200 = close_df.rolling(200, min_periods=200).mean().to_numpy()
    dv63 = dv_df.rolling(63, min_periods=32).median().to_numpy()
    score = np.full_like(cl, np.nan)
    score[:len(p.dates)] = hist_score
    tri = np.ones_like(cl)
    tri[:len(p.dates)] = p.total_return_index
    last, previous_close = p.total_return_index[-1].copy(), p.adj_close[-1].copy()
    for j in range(len(ext_dates)):
        i = len(p.dates) + j
        step = np.divide(cl[i], previous_close, out=np.ones(cl.shape[1]),
                         where=np.isfinite(cl[i]) & np.isfinite(previous_close) & (previous_close > 0))
        last = last * step
        tri[i] = last
        previous_close = cl[i]
        if i >= 147:
            score[i] = tri[i - 21] / tri[i - 147] - 1.0
    mask = np.zeros_like(cl, dtype=bool)
    mask[:len(p.dates)] = hist_mask
    for j in range(len(ext_dates)):
        i = len(p.dates) + j
        ready = ext_member[j] & np.isfinite(cl[i]) & (sma50[i] > sma200[i]) & np.isfinite(score[i]) & np.isfinite(dv63[i])
        eligible = np.flatnonzero(ready)
        keep = max(1, int(np.ceil(len(eligible) * 0.5))) if len(eligible) else 0
        liquid = eligible[np.argsort(dv63[i, eligible], kind="stable")[-keep:]] if keep else np.array([], dtype=int)
        mask[i, liquid] = True
    return dates, returns, score, mask, membership_max


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    dates, returns, score, mask, membership_max = panel()
    rows = []
    for n in range(1, 21):
        daily, metrics = simulate(dates, returns, score, mask, n)
        rows.append(metrics)
        daily.to_parquet(OUT / f"daily_top{n}.parquet")
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / "compounded_breadth.csv", index=False)
    report = {
        "status": "completed", "window_start": str(START.date()), "window_end": str(END.date()),
        "observed_oos_start": "2026-05-01", "maximum_loaded_date": str(dates.max().date()),
        "membership_maximum_date": str(membership_max.date()),
        "accounting": "self_financing_compounded_change_only_equalization_0.5pct_cash_reserve",
        "execution": "bar_plus_frozen_average_slippage_approximation",
        "metrics": rows,
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(frame.to_string(index=False))


if __name__ == "__main__":
    main()
