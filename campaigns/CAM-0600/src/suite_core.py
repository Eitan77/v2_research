from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import duckdb
import numpy as np
import pandas as pd


WORKSPACE = Path(__file__).resolve().parents[3]
CAMPAIGNS = WORKSPACE / "campaigns"
CATALOG = Path(r"D:\AlgoResearch\data\catalog.duckdb")
CUTOFF = pd.Timestamp("2026-04-30")
HOLDOUT = pd.Timestamp("2026-05-01")
COSTS_BPS = (-1.0, 0.0, 1.0, 2.0, 5.0, 10.0)
ETF_SYMBOLS = (
    "ARKK", "BIL", "DIA", "GLD", "HYG", "IWM", "QQQ", "SHY", "SMH", "SOXL", "SOXS",
    "SPY", "SQQQ", "TLT", "TQQQ", "USO", "XLE", "XLF", "XLI", "XLK",
    "XLB", "XLC", "XLP", "XLRE", "XLU", "XLV", "XLY",
)
SECTOR_ETFS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return str(value)
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(payload), indent=2) + "\n", encoding="utf-8")


@dataclass
class Panel:
    name: str
    dates: pd.DatetimeIndex
    symbols: np.ndarray
    member: np.ndarray
    raw_open: np.ndarray
    raw_high: np.ndarray
    raw_low: np.ndarray
    raw_close: np.ndarray
    adj_open: np.ndarray
    adj_high: np.ndarray
    adj_low: np.ndarray
    adj_close: np.ndarray
    volume: np.ndarray
    split_grid: np.ndarray
    split_factor: np.ndarray
    dividend_grid: np.ndarray
    total_return_index: np.ndarray
    open_to_next_open_return: np.ndarray
    open_to_close_return: np.ndarray
    readiness: dict[str, Any]

    @property
    def n_dates(self) -> int:
        return len(self.dates)

    @property
    def n_symbols(self) -> int:
        return len(self.symbols)

    @property
    def symbol_to_col(self) -> dict[str, int]:
        return {str(s): i for i, s in enumerate(self.symbols)}


def _total_return_arrays(
    adj_open: np.ndarray,
    adj_close: np.ndarray,
    dividend_grid: np.ndarray,
    split_factor: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    dividend_adjusted = np.nan_to_num(dividend_grid * split_factor, nan=0.0)
    close_ret = np.full_like(adj_close, np.nan, dtype=float)
    valid = (
        np.isfinite(adj_close[1:])
        & np.isfinite(adj_close[:-1])
        & (adj_close[:-1] > 0)
    )
    tmp = np.full_like(adj_close[1:], np.nan, dtype=float)
    tmp[valid] = (
        (adj_close[1:][valid] + dividend_adjusted[1:][valid])
        / adj_close[:-1][valid]
        - 1.0
    )
    close_ret[1:] = tmp
    tri = np.ones_like(adj_close, dtype=float)
    for i in range(1, len(tri)):
        step = np.where(np.isfinite(close_ret[i]), 1.0 + close_ret[i], 1.0)
        tri[i] = tri[i - 1] * step
    oo = np.full_like(adj_open, np.nan, dtype=float)
    valid_oo = (
        np.isfinite(adj_open[:-1])
        & np.isfinite(adj_open[1:])
        & (adj_open[:-1] > 0)
    )
    tmp_oo = np.full_like(adj_open[:-1], np.nan, dtype=float)
    tmp_oo[valid_oo] = (
        (adj_open[1:][valid_oo] + dividend_adjusted[1:][valid_oo])
        / adj_open[:-1][valid_oo]
        - 1.0
    )
    oo[:-1] = tmp_oo
    oc = np.where(
        np.isfinite(adj_open) & np.isfinite(adj_close) & (adj_open > 0),
        adj_close / adj_open - 1.0,
        np.nan,
    )
    return tri, oo, oc


def _panel_from_existing(name: str, data: dict[str, Any], readiness: dict[str, Any]) -> Panel:
    tri, oo, oc = _total_return_arrays(
        data["adj_open"], data["adj_close"], data["dividend_grid"], data["split_factor"]
    )
    return Panel(
        name=name,
        dates=pd.DatetimeIndex(data["dates"]),
        symbols=np.asarray(data["symbols"]),
        member=np.asarray(data["member"], dtype=bool),
        raw_open=np.asarray(data["raw_open"], dtype=float),
        raw_high=np.asarray(data["raw_high"], dtype=float),
        raw_low=np.asarray(data["raw_low"], dtype=float),
        raw_close=np.asarray(data["raw_close"], dtype=float),
        adj_open=np.asarray(data["adj_open"], dtype=float),
        adj_high=np.asarray(data["adj_high"], dtype=float),
        adj_low=np.asarray(data["adj_low"], dtype=float),
        adj_close=np.asarray(data["adj_close"], dtype=float),
        volume=np.asarray(data["raw_volume"], dtype=float),
        split_grid=np.asarray(data["split_grid"], dtype=float),
        split_factor=np.asarray(data["split_factor"], dtype=float),
        dividend_grid=np.asarray(data["dividend_grid"], dtype=float),
        total_return_index=tri,
        open_to_next_open_return=oo,
        open_to_close_return=oc,
        readiness=readiness,
    )


def _load_etf_panel() -> Panel:
    sys.path.insert(0, str(CAMPAIGNS / "CAM-0513" / "src"))
    import run_0001_quality_sma as base

    with duckdb.connect(str(CATALOG), read_only=True) as con:
        con.execute("SET threads=16")
        bars = con.execute(
            """
            SELECT symbol, date, open, high, low, close, volume
            FROM bars_1d
            WHERE feed='sip'
              AND adjustment='split'
              AND date <= DATE '2026-04-30'
            ORDER BY date, symbol
            """
        ).df()
    bars["date"] = pd.to_datetime(bars["date"]).dt.tz_localize(None)
    supplemental_path = CAMPAIGNS / "CAM-0600" / "artifacts" / "shared" / "supplemental_etf_daily.parquet"
    if supplemental_path.exists():
        supplemental = pd.read_parquet(supplemental_path)
        supplemental["date"] = pd.to_datetime(supplemental["date"]).dt.tz_localize(None)
        if (supplemental["date"] >= HOLDOUT).any():
            raise RuntimeError("supplemental ETF data crossed the holdout")
        supplemental = supplemental[["symbol", "date", "open", "high", "low", "close", "volume"]]
        bars = pd.concat([bars, supplemental], ignore_index=True)
    if bars.empty or bars["date"].max() != CUTOFF:
        raise RuntimeError("ETF daily panel is empty or does not reach cutoff")
    if (bars["date"] >= HOLDOUT).any() or bars.duplicated(["date", "symbol"]).any():
        raise RuntimeError("ETF cutoff or duplicate-key readiness failed")
    symbols = np.asarray(sorted(bars["symbol"].astype(str).unique()))
    spy = bars[bars["symbol"] == "SPY"].sort_values("date")
    dates = pd.DatetimeIndex(spy["date"])
    pivot: dict[str, np.ndarray] = {}
    for field in ("open", "high", "low", "close", "volume"):
        pivot[field] = (
            bars.pivot(index="date", columns="symbol", values=field)
            .reindex(index=dates, columns=symbols)
            .to_numpy(float)
        )
    member = np.isfinite(pivot["close"])
    split_grid = np.ones_like(pivot["close"], dtype=float)
    split_factor = np.ones_like(split_grid)
    dividend_grid = np.zeros_like(split_grid)
    _, _, _, actions = base._read_source_frames()
    actions["ex_date"] = pd.to_datetime(actions["ex_date"]).dt.tz_localize(None)
    symbol_to_col = {str(s): i for i, s in enumerate(symbols)}
    dividend_rows = 0
    for row in actions.itertuples(index=False):
        if getattr(row, "action_kind", None) != "dividend":
            continue
        symbol = str(row.symbol)
        if symbol in {"BIL", "SHY", "XLB", "XLC", "XLRE"}:
            # Supplemental bars use Alpaca adjustment=all and already include distributions.
            continue
        if symbol not in symbol_to_col or pd.isna(row.ex_date):
            continue
        idx = int(dates.searchsorted(pd.Timestamp(row.ex_date), side="left"))
        if idx >= len(dates):
            continue
        rate = float(row.dividend_rate) if pd.notna(row.dividend_rate) else np.nan
        if np.isfinite(rate):
            dividend_grid[idx, symbol_to_col[symbol]] += rate
            dividend_rows += 1
    tri, oo, oc = _total_return_arrays(
        pivot["open"], pivot["close"], dividend_grid, split_factor
    )
    readiness = {
        "status": "passed",
        "rows_loaded": int(len(bars)),
        "symbols_loaded": int(len(symbols)),
        "symbols": symbols.tolist(),
        "min_date": str(dates.min().date()),
        "max_date": str(dates.max().date()),
        "duplicate_keys": 0,
        "holdout_rows_loaded": 0,
        "dividend_rows_loaded": int(dividend_rows),
    }
    return Panel(
        name="etf",
        dates=dates,
        symbols=symbols,
        member=member,
        raw_open=pivot["open"],
        raw_high=pivot["high"],
        raw_low=pivot["low"],
        raw_close=pivot["close"],
        adj_open=pivot["open"],
        adj_high=pivot["high"],
        adj_low=pivot["low"],
        adj_close=pivot["close"],
        volume=pivot["volume"],
        split_grid=split_grid,
        split_factor=split_factor,
        dividend_grid=dividend_grid,
        total_return_index=tri,
        open_to_next_open_return=oo,
        open_to_close_return=oc,
        readiness=readiness,
    )


def load_panels() -> dict[str, Panel]:
    sys.path.insert(0, str(CAMPAIGNS / "CAM-0513" / "src"))
    sys.path.insert(0, str(CAMPAIGNS / "CAM-0515" / "src"))
    import run_0001_quality_sma as qqq_loader
    import run_0007_sp500_top5 as sp_loader

    qqq_data, qqq_readiness = qqq_loader.load_data()
    sp_data, sp_readiness = sp_loader._build_sp500_data()
    panels = {
        "qqq": _panel_from_existing("qqq", qqq_data, qqq_readiness),
        "sp500": _panel_from_existing("sp500", sp_data, sp_readiness),
        "etf": _load_etf_panel(),
    }
    for panel in panels.values():
        if panel.dates.max() > CUTOFF:
            raise RuntimeError(f"{panel.name} loaded holdout date {panel.dates.max()}")
        if panel.member.shape != panel.adj_close.shape:
            raise RuntimeError(f"{panel.name} membership shape mismatch")
    return panels


def month_end_indices(dates: pd.DatetimeIndex) -> np.ndarray:
    periods = dates.to_period("M")
    return np.asarray(
        [i for i in range(len(dates)) if i == len(dates) - 1 or periods[i + 1] != periods[i]],
        dtype=int,
    )


def weekly_indices(dates: pd.DatetimeIndex) -> np.ndarray:
    periods = dates.to_period("W-FRI")
    return np.asarray(
        [i for i in range(len(dates)) if i == len(dates) - 1 or periods[i + 1] != periods[i]],
        dtype=int,
    )


def forward_fill_signal_weights(weights: np.ndarray, signal_indices: Iterable[int]) -> np.ndarray:
    out = np.zeros_like(weights, dtype=float)
    last = np.zeros(weights.shape[1], dtype=float)
    signal_set = set(int(x) for x in signal_indices)
    for i in range(len(out)):
        if i in signal_set:
            last = weights[i].copy()
        out[i] = last
    return out


def rank_weights(
    scores: np.ndarray,
    eligible: np.ndarray,
    signal_indices: Iterable[int],
    *,
    mode: str,
    quantile: float = 0.10,
    top_k: int | None = None,
    inverse_vol: np.ndarray | None = None,
) -> np.ndarray:
    weights = np.zeros_like(scores, dtype=float)
    for i in signal_indices:
        mask = eligible[i] & np.isfinite(scores[i])
        cols = np.flatnonzero(mask)
        if not len(cols):
            continue
        order = cols[np.argsort(scores[i, cols], kind="stable")]
        n = top_k if top_k is not None else max(1, int(np.ceil(len(order) * quantile)))
        n = min(n, len(order))
        low = order[:n]
        high = order[-n:]

        def allocate(chosen: np.ndarray, gross: float, sign: float) -> None:
            if not len(chosen):
                return
            if inverse_vol is None:
                local = np.ones(len(chosen), dtype=float)
            else:
                local = np.where(
                    np.isfinite(inverse_vol[i, chosen]) & (inverse_vol[i, chosen] > 0),
                    1.0 / inverse_vol[i, chosen],
                    0.0,
                )
                if local.sum() <= 0:
                    local = np.ones(len(chosen), dtype=float)
            local = local / local.sum() * gross * sign
            weights[i, chosen] = local

        if mode == "long":
            allocate(high, 1.0, 1.0)
        elif mode == "short":
            allocate(low, 1.0, -1.0)
        elif mode == "long_short":
            allocate(high, 0.5, 1.0)
            allocate(low, 0.5, -1.0)
        elif mode == "reversal_long":
            allocate(low, 1.0, 1.0)
        elif mode == "reversal_long_short":
            allocate(low, 0.5, 1.0)
            allocate(high, 0.5, -1.0)
        else:
            raise ValueError(mode)
    return forward_fill_signal_weights(weights, signal_indices)


def trailing_vol(panel: Panel, window: int) -> np.ndarray:
    ret = pd.DataFrame(panel.total_return_index, index=panel.dates).pct_change()
    return ret.rolling(window, min_periods=window).std(ddof=1).to_numpy(float) * np.sqrt(252.0)


def trailing_return(panel: Panel, lookback: int, skip: int = 0) -> np.ndarray:
    tri = panel.total_return_index
    out = np.full_like(tri, np.nan, dtype=float)
    right = skip
    left = lookback + skip
    for i in range(left, len(tri)):
        out[i] = tri[i - right] / tri[i - left] - 1.0
    return out


def drawdown_metrics(equity: pd.Series) -> dict[str, Any]:
    if equity.empty:
        return {"maximum_drawdown": 0.0, "drawdown_recovery_sessions": None}
    values = equity.to_numpy(float)
    peaks = np.maximum.accumulate(values)
    dd = (peaks - values) / peaks
    trough = int(np.argmax(dd))
    peak = int(np.argmax(values[: trough + 1]))
    recovery = None
    for i in range(trough + 1, len(values)):
        if values[i] >= values[peak]:
            recovery = int(i - trough)
            break
    return {
        "maximum_drawdown": float(dd[trough]),
        "drawdown_peak_date": str(equity.index[peak].date()),
        "drawdown_trough_date": str(equity.index[trough].date()),
        "drawdown_recovery_sessions": recovery,
        "ending_drawdown": float(dd[-1]),
    }


def _period_views(daily: pd.Series) -> tuple[pd.Series, pd.Series]:
    monthly = daily.groupby(daily.index.to_period("M")).sum()
    yearly = daily.groupby(daily.index.year).sum()
    monthly.index = monthly.index.astype(str)
    yearly.index = yearly.index.astype(str)
    return monthly, yearly


def _recent_month_metrics(monthly: pd.Series, n: int) -> dict[str, Any]:
    x = monthly.iloc[-n:] if len(monthly) >= n else monthly
    return {
        f"recent{n}_months_observed": int(len(x)),
        f"recent{n}_average_month": float(x.mean()) if len(x) else 0.0,
        f"recent{n}_median_month": float(x.median()) if len(x) else 0.0,
        f"recent{n}_positive_months": int((x > 1e-12).sum()),
        f"recent{n}_negative_months": int((x < -1e-12).sum()),
        f"recent{n}_inactive_months": int((x.abs() <= 1e-12).sum()),
    }


def evaluate_weights(
    panel: Panel,
    signal_weights: np.ndarray,
    cost_bps_per_side: float,
    *,
    holding: str = "open_to_next_open",
    execution_lag: int = 1,
    return_override: np.ndarray | None = None,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if signal_weights.shape != panel.adj_close.shape:
        raise ValueError("weight shape mismatch")
    executed = np.zeros_like(signal_weights, dtype=float)
    if execution_lag == 1:
        executed[1:] = signal_weights[:-1]
    elif execution_lag == 0:
        executed[:] = signal_weights
    else:
        raise ValueError("execution_lag must be zero or one")
    entry_available = np.isfinite(panel.adj_open)
    unavailable_entries = int(((np.abs(executed) > 0) & ~entry_available).sum())
    executed = np.where(entry_available, executed, 0.0)
    if return_override is not None:
        if return_override.shape != panel.adj_close.shape:
            raise ValueError("return override shape mismatch")
        returns = return_override
    elif holding == "open_to_next_open":
        returns = panel.open_to_next_open_return
        returns = returns.copy()
        returns[-1] = panel.open_to_close_return[-1]
    elif holding == "open_to_close":
        returns = panel.open_to_close_return
    else:
        raise ValueError(holding)
    missing_return_positions = int(((np.abs(executed) > 0) & ~np.isfinite(returns)).sum())
    gross_symbol = executed * np.nan_to_num(returns, nan=0.0)
    delta = np.vstack([executed[0], np.diff(executed, axis=0)])
    if holding in ("open_to_close", "return_override"):
        turnover_symbol = 2.0 * np.abs(executed)
    else:
        turnover_symbol = np.abs(delta)
    cost_symbol = turnover_symbol * float(cost_bps_per_side) / 10000.0
    net_symbol = gross_symbol - cost_symbol
    daily_gross = gross_symbol.sum(axis=1)
    daily_cost = cost_symbol.sum(axis=1)
    daily_net = net_symbol.sum(axis=1)
    daily = pd.DataFrame(
        {
            "date": panel.dates,
            "gross_pnl": daily_gross,
            "cost": daily_cost,
            "net_pnl": daily_net,
            "equity": 1.0 + np.cumsum(daily_net),
            "gross_exposure": np.abs(executed).sum(axis=1),
            "net_exposure": executed.sum(axis=1),
            "turnover": turnover_symbol.sum(axis=1),
        }
    ).set_index("date")
    monthly, yearly = _period_views(daily["net_pnl"])
    symbol_pnl = pd.Series(net_symbol.sum(axis=0), index=panel.symbols.astype(str)).sort_values(ascending=False)
    positive_total = float(symbol_pnl.clip(lower=0).sum())
    top5_symbol_positive_share = (
        float(symbol_pnl.clip(lower=0).head(5).sum() / positive_total)
        if positive_total > 0 else None
    )
    positive_days = pd.Series(daily_net, index=panel.dates).clip(lower=0).sort_values(ascending=False)
    total_positive_days = float(positive_days.sum())
    top5_day_positive_share = (
        float(positive_days.head(5).sum() / total_positive_days)
        if total_positive_days > 0 else None
    )
    if holding in ("open_to_close", "return_override"):
        entries = int((np.abs(executed) > 1e-12).sum())
    else:
        entries = int(((np.abs(executed) > 1e-12) & (np.abs(np.vstack([np.zeros(executed.shape[1]), executed[:-1]])) <= 1e-12)).sum())
    metrics: dict[str, Any] = {
        "panel": panel.name,
        "execution_lag_bars": int(execution_lag),
        "cost_bps_per_side": float(cost_bps_per_side),
        "net_simple_return": float(daily_net.sum()),
        "gross_simple_return": float(daily_gross.sum()),
        "total_cost": float(daily_cost.sum()),
        "entries": entries,
        "position_change_count": int((np.abs(delta) > 1e-12).sum()),
        "active_days": int((np.abs(executed).sum(axis=1) > 1e-12).sum()),
        "green_days": int((daily_net > 1e-12).sum()),
        "red_days": int((daily_net < -1e-12).sum()),
        "average_gross_exposure": float(np.abs(executed).sum(axis=1).mean()),
        "maximum_gross_exposure": float(np.abs(executed).sum(axis=1).max()),
        "total_turnover": float(turnover_symbol.sum()),
        "profitable_symbols": int((symbol_pnl > 0).sum()),
        "loss_symbols": int((symbol_pnl < 0).sum()),
        "top5_symbol_positive_share": top5_symbol_positive_share,
        "top5_day_positive_share": top5_day_positive_share,
        "best_symbol": str(symbol_pnl.index[0]) if len(symbol_pnl) else None,
        "best_symbol_pnl": float(symbol_pnl.iloc[0]) if len(symbol_pnl) else None,
        "leave_best_symbol_out_return": float(daily_net.sum() - symbol_pnl.iloc[0]) if len(symbol_pnl) else None,
        "unavailable_entry_positions": unavailable_entries,
        "missing_return_positions": missing_return_positions,
        "monthly_average": float(monthly.mean()) if len(monthly) else 0.0,
        "monthly_median": float(monthly.median()) if len(monthly) else 0.0,
        "positive_months": int((monthly > 1e-12).sum()),
        "negative_months": int((monthly < -1e-12).sum()),
        "inactive_months": int((monthly.abs() <= 1e-12).sum()),
        **drawdown_metrics(daily["equity"]),
        **_recent_month_metrics(monthly, 12),
        **_recent_month_metrics(monthly, 15),
        **_recent_month_metrics(monthly, 18),
    }
    monthly_df = monthly.rename("net_pnl").to_frame()
    yearly_df = yearly.rename("net_pnl").to_frame()
    symbols_df = symbol_pnl.rename("net_pnl").to_frame()
    return metrics, daily, monthly_df, yearly_df, symbols_df


def save_variant(
    output_dir: Path,
    variant_id: str,
    metrics: dict[str, Any],
    daily: pd.DataFrame,
    monthly: pd.DataFrame,
    yearly: pd.DataFrame,
    symbols: pd.DataFrame,
    *,
    save_detail: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if save_detail:
        safe = variant_id.replace("/", "_").replace(":", "_")
        detail = output_dir / "variants" / safe
        detail.mkdir(parents=True, exist_ok=True)
        daily.reset_index().to_parquet(detail / "daily.parquet", index=False)
        monthly.reset_index().to_csv(detail / "monthly.csv", index=False)
        yearly.reset_index().to_csv(detail / "yearly.csv", index=False)
        symbols.reset_index(names="symbol").to_csv(detail / "symbols.csv", index=False)
        write_json(detail / "metrics.json", metrics)


def semantic_fixtures() -> dict[str, Any]:
    equity = pd.Series([1.0, 1.1, 1.05, 1.2], index=pd.date_range("2020-01-01", periods=4))
    dd = drawdown_metrics(equity)["maximum_drawdown"]
    expected = 0.05 / 1.1
    if abs(dd - expected) > 1e-12:
        raise RuntimeError("drawdown fixture failed")
    scores = np.asarray([[1.0, 2.0, 3.0, 4.0]])
    eligible = np.ones_like(scores, dtype=bool)
    w = rank_weights(scores, eligible, [0], mode="long_short", quantile=0.25)
    if abs(np.abs(w).sum() - 1.0) > 1e-12 or abs(w.sum()) > 1e-12:
        raise RuntimeError("rank-weight fixture failed")
    return {"status": "passed", "drawdown": dd, "rank_weight_gross": float(np.abs(w).sum())}
