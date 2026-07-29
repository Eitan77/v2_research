from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

import duckdb
import numpy as np
import pandas as pd


CUTOFF = pd.Timestamp("2026-04-30")
HOLDOUT_START = pd.Timestamp("2026-05-01")
CATALOG = Path(r"D:\AlgoResearch\data\catalog.duckdb")
FEATURES = [
    "log_price",
    "log_dollar_volume",
    "reversal_1d",
    "momentum_5d",
    "momentum_20d",
    "volatility_20d",
    "beta_60d",
]


def validate_cutoff(frame: pd.DataFrame, date_column: str = "date") -> None:
    dates = pd.to_datetime(frame[date_column])
    if frame.empty:
        raise RuntimeError("no rows loaded")
    if dates.max() > CUTOFF or int((dates >= HOLDOUT_START).sum()):
        raise RuntimeError("sealed holdout row detected")


def stable_frame_hash(frame: pd.DataFrame, columns: Iterable[str]) -> str:
    ordered = frame.sort_values(list(columns)).reset_index(drop=True)
    payload = ordered.to_csv(index=False, float_format="%.12g", lineterminator="\n")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def paper_rank_normalize(values: pd.Series) -> pd.Series:
    """Equation (4)-(5): rank/(n+1), demean, divide by sum absolute deviations."""
    result = pd.Series(np.nan, index=values.index, dtype=float)
    valid = values.dropna()
    if len(valid) < 2:
        return result
    transformed = valid.rank(method="average") / (len(valid) + 1.0)
    centered = transformed - transformed.mean()
    denominator = centered.abs().sum()
    if denominator <= 0:
        return result
    result.loc[valid.index] = centered / denominator
    return result


def source_style_residual(
    returns: pd.Series, characteristics: pd.DataFrame
) -> tuple[pd.Series, pd.Series, float]:
    """
    Fit RET = alpha + X beta. The paper defines RISK = X beta (no intercept)
    and RESIDUAL = RET - RISK, so the intercept remains in RESIDUAL.
    """
    joined = pd.concat(
        [returns.rename("ret"), characteristics], axis=1
    ).dropna()
    residual = pd.Series(np.nan, index=returns.index, dtype=float)
    risk = pd.Series(np.nan, index=returns.index, dtype=float)
    if len(joined) <= characteristics.shape[1] + 2:
        return risk, residual, np.nan
    y = joined["ret"].to_numpy(dtype=float)
    x = joined[characteristics.columns].to_numpy(dtype=float)
    design = np.column_stack([np.ones(len(x)), x])
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    alpha = float(coefficients[0])
    fitted_risk = x @ coefficients[1:]
    risk.loc[joined.index] = fitted_risk
    residual.loc[joined.index] = y - fitted_risk
    return risk, residual, alpha


def assign_tail_portfolios(
    residual: pd.Series, groups: int = 10
) -> pd.Series:
    result = pd.Series(pd.NA, index=residual.index, dtype="Int64")
    valid = residual.dropna()
    if len(valid) < groups:
        return result
    ranks = valid.rank(method="first")
    result.loc[valid.index] = (
        np.floor((ranks - 1) * groups / len(valid)).astype(int) + 1
    )
    return result


def max_drawdown_and_recovery(daily: pd.DataFrame) -> tuple[float, int, bool]:
    ordered = daily.sort_values("date")
    equity = 1.0 + ordered["net_pnl"].cumsum()
    peak = equity.cummax()
    drawdown = (peak - equity) / peak
    max_dd = float(drawdown.max()) if len(drawdown) else 0.0
    start = None
    longest = 0
    for date, value in zip(pd.to_datetime(ordered["date"]), drawdown):
        if value > 1e-12 and start is None:
            start = date
        elif value <= 1e-12 and start is not None:
            longest = max(longest, int((date - start).days))
            start = None
    unresolved = start is not None
    if unresolved:
        longest = max(
            longest,
            int((pd.to_datetime(ordered["date"]).iloc[-1] - start).days),
        )
    return max_dd, longest, unresolved


def long_net_return(
    entry: float, exit_: float, cost_bps_per_side: float
) -> float:
    if entry <= 0 or exit_ <= 0:
        raise ValueError("prices must be positive")
    return exit_ / entry - 1.0 - 2.0 * cost_bps_per_side / 10_000.0


def protected_short_net_return(
    entry: float,
    scheduled_exit: float,
    path_high: float,
    cost_bps_per_side: float,
    stop_fraction: float = 0.02,
    stop_slippage_bps: float = 5.0,
) -> tuple[float, bool, float]:
    if min(entry, scheduled_exit, path_high) <= 0:
        raise ValueError("prices must be positive")
    stop = entry * (1.0 + stop_fraction)
    stopped = path_high >= stop
    exit_ = (
        stop * (1.0 + stop_slippage_bps / 10_000.0)
        if stopped
        else scheduled_exit
    )
    result = (entry - exit_) / entry - 2.0 * cost_bps_per_side / 10_000.0
    return result, stopped, exit_


def load_membership(
    catalog: Path = CATALOG,
    start: str = "2024-05-01",
    end: str = "2026-04-30",
) -> pd.DataFrame:
    con = duckdb.connect(str(catalog), read_only=True)
    try:
        frame = con.execute(
            """
            SELECT try_cast(date AS DATE) AS date, symbol, security_id,
                   known_at_ts, membership_source_quality
            FROM interday_qqq_membership_daily_v1
            WHERE is_member
              AND try_cast(date AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
            ORDER BY date, symbol
            """,
            [start, end],
        ).fetchdf()
    finally:
        con.close()
    frame["date"] = pd.to_datetime(frame["date"])
    validate_cutoff(frame)
    if frame.duplicated(["date", "symbol"]).any():
        raise RuntimeError("duplicate membership key")
    return frame


def load_regular_30m(
    catalog: Path = CATALOG,
    start: str = "2024-05-01",
    end: str = "2026-04-30",
) -> pd.DataFrame:
    con = duckdb.connect(str(catalog), read_only=True)
    try:
        frame = con.execute(
            """
            WITH members AS (
              SELECT try_cast(date AS DATE) AS date, symbol, known_at_ts
              FROM interday_qqq_membership_daily_v1
              WHERE is_member
                AND try_cast(date AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
            ), ranked AS (
              SELECT b.*,
                row_number() OVER (
                  PARTITION BY b.symbol,b.bar_start_ts,b.feed,b.adjustment
                  ORDER BY coalesce(try_cast(b.ingested_at AS TIMESTAMP),
                                    TIMESTAMP '1900-01-01') DESC,
                           coalesce(b.source_ingestion_id,'') DESC
                ) AS rn
              FROM derived_bars_30m b
              JOIN members m
                ON m.date=try_cast(b.session_date AS DATE) AND m.symbol=b.symbol
              WHERE try_cast(b.session_date AS DATE)
                    BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                AND b.feed='sip' AND b.adjustment='raw' AND b.bar_complete
            )
            SELECT symbol,try_cast(session_date AS DATE) AS date,
                   bar_start_ts,bar_end_ts,available_at_ts,
                   open,high,low,close,volume,trade_count,vwap
            FROM ranked
            WHERE rn=1
              AND strftime(bar_start_ts AT TIME ZONE 'America/New_York','%H:%M')
                  IN ('09:30','10:00','10:30','11:00','11:30','12:00','12:30',
                      '13:00','13:30','14:00','14:30','15:00','15:30')
            ORDER BY date,symbol,bar_start_ts
            """,
            [start, end, start, end],
        ).fetchdf()
    finally:
        con.close()
    frame["date"] = pd.to_datetime(frame["date"])
    validate_cutoff(frame)
    if frame.duplicated(["symbol", "bar_start_ts"]).any():
        raise RuntimeError("duplicate 30-minute bar key")
    return frame


def load_split_daily(
    symbols: Iterable[str],
    catalog: Path = CATALOG,
    start: str = "2024-05-01",
    end: str = "2026-04-30",
) -> tuple[pd.DataFrame, dict]:
    symbols = sorted(set(symbols) | {"QQQ"})
    con = duckdb.connect(str(catalog), read_only=True)
    try:
        source = con.execute(
            """
            WITH ranked AS (
              SELECT symbol,try_cast(date AS DATE) AS date,open,high,low,close,
                     volume,trade_count,vwap,adjustment,source_ingestion_id,ingested_at,
                row_number() OVER (
                  PARTITION BY symbol,try_cast(date AS DATE),feed,adjustment
                  ORDER BY coalesce(try_cast(ingested_at AS TIMESTAMP),
                                    TIMESTAMP '1900-01-01') DESC,
                           coalesce(source_ingestion_id,'') DESC
                ) rn
              FROM bars_1d
              WHERE try_cast(date AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                AND symbol IN (SELECT UNNEST(?))
                AND feed='sip' AND adjustment IN ('raw','split')
            )
            SELECT * EXCLUDE(rn) FROM ranked WHERE rn=1
            ORDER BY date,symbol,adjustment
            """,
            [start, end, symbols],
        ).fetchdf()
    finally:
        con.close()
    source["date"] = pd.to_datetime(source["date"])
    validate_cutoff(source)
    raw = source[source["adjustment"] == "raw"].copy()
    split = source[source["adjustment"] == "split"].copy()
    raw_keys = set(map(tuple, raw[["symbol", "date"]].to_numpy()))
    split_keys = set(map(tuple, split[["symbol", "date"]].to_numpy()))
    missing_keys = sorted(raw_keys - split_keys, key=lambda x: (x[1], x[0]))
    reconstructed = []
    drifts = []
    for symbol, date in missing_keys:
        raw_symbol = raw[raw["symbol"] == symbol].set_index("date")
        split_symbol = split[split["symbol"] == symbol].set_index("date")
        prior = split_symbol.index[split_symbol.index < date]
        following = split_symbol.index[split_symbol.index > date]
        if len(prior) == 0 or len(following) == 0:
            continue
        prior_factor = float(
            split_symbol.loc[prior.max(), "close"] / raw_symbol.loc[prior.max(), "close"]
        )
        next_factor = float(
            split_symbol.loc[following.min(), "close"]
            / raw_symbol.loc[following.min(), "close"]
        )
        drift = abs(next_factor / prior_factor - 1.0)
        drifts.append(drift)
        if drift > 0.001:
            raise RuntimeError(
                f"split factor changes across missing row {symbol} {date.date()}: {drift}"
            )
        row = raw_symbol.loc[date].copy()
        factor = (prior_factor + next_factor) / 2.0
        for column in ["open", "high", "low", "close", "vwap"]:
            row[column] = float(row[column]) * factor
        row["symbol"] = symbol
        row["date"] = date
        row["adjustment"] = "split_reconstructed_from_raw"
        reconstructed.append(row)
    if reconstructed:
        split = pd.concat([split, pd.DataFrame(reconstructed)], ignore_index=True)
    split["reconstructed_from_raw"] = split["adjustment"].eq(
        "split_reconstructed_from_raw"
    )
    split = split.sort_values(["date", "symbol"]).reset_index(drop=True)
    report = {
        "raw_rows": int(len(raw)),
        "native_split_rows": int((split["reconstructed_from_raw"] == False).sum()),
        "reconstructed_rows": int(split["reconstructed_from_raw"].sum()),
        "unreconstructable_edge_rows": int(len(missing_keys) - len(reconstructed)),
        "maximum_neighbor_factor_drift": max(drifts, default=0.0),
    }
    return split, report


def build_daily_features(daily: pd.DataFrame) -> pd.DataFrame:
    frame = daily.copy().sort_values(["symbol", "date"])
    frame["daily_return"] = frame.groupby("symbol")["close"].pct_change()
    qqq = (
        frame[frame["symbol"] == "QQQ"][["date", "daily_return"]]
        .rename(columns={"daily_return": "market_return"})
        .dropna()
    )
    frame = frame.merge(qqq, on="date", how="left")
    frame["log_price"] = np.log(frame["close"])
    frame["log_dollar_volume"] = np.log(
        (frame["close"] * frame["volume"]).clip(lower=1.0)
    )
    frame["reversal_1d"] = frame["daily_return"]
    frame["momentum_5d"] = frame.groupby("symbol")["close"].pct_change(5)
    frame["momentum_20d"] = frame.groupby("symbol")["close"].pct_change(20)
    frame["volatility_20d"] = (
        frame.groupby("symbol")["daily_return"]
        .rolling(20, min_periods=15)
        .std()
        .reset_index(level=0, drop=True)
    )

    frame["beta_60d"] = np.nan
    for _, index in frame.groupby("symbol", sort=False).groups.items():
        group = frame.loc[index]
        covariance = group["daily_return"].rolling(60, min_periods=40).cov(
            group["market_return"]
        )
        variance = group["market_return"].rolling(60, min_periods=40).var()
        frame.loc[index, "beta_60d"] = (covariance / variance).to_numpy()
    feature_columns = ["symbol", "date", *FEATURES]
    features = frame[feature_columns].copy()
    features[FEATURES] = features.groupby("symbol")[FEATURES].shift(1)
    return features
