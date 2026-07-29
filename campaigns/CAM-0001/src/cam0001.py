from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import duckdb
import numpy as np
import pandas as pd


CUTOFF = pd.Timestamp("2026-04-30")
HOLDOUT_START = pd.Timestamp("2026-05-01")
CATALOG = Path(r"D:\AlgoResearch\data\catalog.duckdb")
PROPOSED_FUNDS = ["TQQQ", "UPRO", "SPXL", "SOXL", "TECL", "FAS", "TNA"]
REQUIRED_SYMBOLS = ["QQQ", "TQQQ", "SOXL"]
TRADE_SYMBOLS = ["TQQQ", "SOXL"]
ALLOWED_ADAPTATION_SYMBOLS = ["QQQ", "SMH", "TQQQ", "SOXL", "SQQQ", "SOXS"]


def _stable_frame_hash(frame: pd.DataFrame) -> str:
    ordered = frame.sort_values(["date", "symbol"]).reset_index(drop=True)
    payload = ordered.to_csv(index=False, float_format="%.12g", lineterminator="\n")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_cutoff_bars(
    catalog: Path = CATALOG, symbols: Iterable[str] = REQUIRED_SYMBOLS
) -> tuple[pd.DataFrame, dict]:
    """Load cutoff-bounded bars and reconcile sparse split-stream omissions."""
    symbols = list(symbols)
    con = duckdb.connect(str(catalog), read_only=True)
    try:
        source = con.execute(
            """
            SELECT symbol, date, open, high, low, close, volume, trade_count,
                   vwap, feed, adjustment, source_ingestion_id, ingested_at
            FROM bars_1d
            WHERE date <= CAST(? AS DATE)
              AND symbol IN (SELECT UNNEST(?))
              AND adjustment IN ('raw', 'split')
              AND feed = 'sip'
            ORDER BY date, symbol, adjustment
            """,
            [CUTOFF.date().isoformat(), symbols],
        ).fetchdf()
    finally:
        con.close()
    source["date"] = pd.to_datetime(source["date"])
    raw = source[source["adjustment"] == "raw"].copy()
    split = source[source["adjustment"] == "split"].copy()
    raw_keys = set(map(tuple, raw[["symbol", "date"]].to_numpy()))
    split_keys = set(map(tuple, split[["symbol", "date"]].to_numpy()))
    missing_keys = sorted(raw_keys - split_keys, key=lambda x: (x[1], x[0]))
    reconstructed = []
    factor_drifts = []
    for symbol, date in missing_keys:
        raw_symbol = raw[raw["symbol"] == symbol].set_index("date")
        split_symbol = split[split["symbol"] == symbol].set_index("date")
        prior_dates = split_symbol.index[split_symbol.index < date]
        next_dates = split_symbol.index[split_symbol.index > date]
        if len(prior_dates) == 0 or len(next_dates) == 0:
            raise RuntimeError(f"cannot bracket missing split row {symbol} {date.date()}")
        prior_date = prior_dates.max()
        next_date = next_dates.min()
        prior_factor = float(split_symbol.loc[prior_date, "close"] / raw_symbol.loc[prior_date, "close"])
        next_factor = float(split_symbol.loc[next_date, "close"] / raw_symbol.loc[next_date, "close"])
        drift = abs(next_factor / prior_factor - 1.0)
        factor_drifts.append(drift)
        if drift > 0.001:
            raise RuntimeError(
                f"split factor changes across missing row {symbol} {date.date()}: {drift}"
            )
        factor = (prior_factor + next_factor) / 2.0
        row = raw_symbol.loc[date].copy()
        for column in ["open", "high", "low", "close", "vwap"]:
            row[column] = float(row[column]) * factor
        row["symbol"] = symbol
        row["date"] = date
        row["adjustment"] = "split_reconstructed_from_raw"
        reconstructed.append(row)
    if reconstructed:
        reconstructed_frame = pd.DataFrame(reconstructed)
        frame = pd.concat([split, reconstructed_frame], ignore_index=True, sort=False)
    else:
        frame = split
    frame["reconstructed_from_raw"] = frame["adjustment"].eq("split_reconstructed_from_raw")
    frame = frame.sort_values(["date", "symbol"]).reset_index(drop=True)
    reconciliation = {
        "raw_rows": int(len(raw)),
        "native_split_rows": int(len(split)),
        "reconstructed_rows": int(len(reconstructed)),
        "missing_native_split_dates": sorted(
            {date.date().isoformat() for _, date in missing_keys}
        ),
        "maximum_neighbor_split_factor_relative_drift": max(factor_drifts, default=0.0),
        "method": (
            "For a raw symbol-date absent from the split stream, multiply raw "
            "OHLC/VWAP by the mean of the immediately preceding and following "
            "native split/raw close ratios; require relative ratio drift <= 0.1%. "
            "Volume and trade_count remain raw. The reconstruction is deterministic "
            "and is not a strategy parameter."
        ),
    }
    return frame, reconciliation


def readiness(catalog: Path, output_json: Path, output_cache: Path) -> dict:
    frame, reconciliation = load_cutoff_bars(catalog)
    if frame.empty:
        raise RuntimeError("readiness failed: no rows loaded")

    max_loaded = frame["date"].max()
    holdout_rows = int((frame["date"] >= HOLDOUT_START).sum())
    duplicates = int(frame.duplicated(["symbol", "date"]).sum())
    null_ohlc = int(frame[["open", "high", "low", "close"]].isna().any(axis=1).sum())
    invalid_prices = int(
        (
            (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
            | (frame["high"] < frame[["open", "close"]].max(axis=1))
            | (frame["low"] > frame[["open", "close"]].min(axis=1))
        ).sum()
    )
    symbols = sorted(frame["symbol"].unique().tolist())
    missing_symbols = sorted(set(REQUIRED_SYMBOLS) - set(symbols))

    by_symbol = (
        frame.groupby("symbol")
        .agg(min_date=("date", "min"), max_date=("date", "max"), rows=("date", "size"),
             sessions=("date", "nunique"), null_volume=("volume", lambda x: int(x.isna().sum())))
        .reset_index()
    )
    date_sets = {
        symbol: set(group["date"].tolist()) for symbol, group in frame.groupby("symbol")
    }
    shared_dates = set.intersection(*date_sets.values()) if date_sets else set()
    date_attrition = {
        symbol: len(dates - shared_dates) for symbol, dates in date_sets.items()
    }

    gap_rows = []
    for symbol, group in frame.groupby("symbol"):
        group = group.sort_values("date").copy()
        group["overnight_gap"] = group["open"] / group["close"].shift(1) - 1.0
        for row in group.nlargest(5, "overnight_gap", keep="all").itertuples():
            gap_rows.append(
                {"symbol": symbol, "date": row.date.date().isoformat(),
                 "overnight_gap": float(row.overnight_gap)}
            )
        for row in group.nsmallest(5, "overnight_gap", keep="all").itertuples():
            gap_rows.append(
                {"symbol": symbol, "date": row.date.date().isoformat(),
                 "overnight_gap": float(row.overnight_gap)}
            )

    failures = []
    if max_loaded > CUTOFF:
        failures.append(f"max loaded date {max_loaded.date()} exceeds cutoff")
    if holdout_rows != 0:
        failures.append(f"{holdout_rows} holdout rows in already-filtered frame")
    if missing_symbols:
        failures.append(f"missing required symbols: {missing_symbols}")
    if duplicates:
        failures.append(f"{duplicates} duplicate symbol-date rows")
    if null_ohlc:
        failures.append(f"{null_ohlc} rows with null OHLC")
    if invalid_prices:
        failures.append(f"{invalid_prices} rows with invalid OHLC geometry")
    if any(date_attrition.values()):
        failures.append(f"unaligned symbol sessions: {date_attrition}")

    result = {
        "status": "failed" if failures else "passed",
        "catalog": str(catalog),
        "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
        "source_view": "bars_1d",
        "source_view_filter": (
            "date <= 2026-04-30 AND symbol IN "
            "(QQQ,TQQQ,SOXL) AND adjustment IN (raw,split) AND feed=sip"
        ),
        "max_loaded_date": max_loaded.date().isoformat(),
        "holdout_rows_loaded": holdout_rows,
        "loaded_frame_sha256": _stable_frame_hash(frame),
        "split_stream_reconciliation": reconciliation,
        "schema": {column: str(dtype) for column, dtype in frame.dtypes.items()},
        "symbols": symbols,
        "proposed_trade_symbols": PROPOSED_FUNDS,
        "eligible_trade_symbols": TRADE_SYMBOLS,
        "unavailable_proposed_symbols": sorted(set(PROPOSED_FUNDS) - set(TRADE_SYMBOLS)),
        "symbol_attrition_count": len(PROPOSED_FUNDS) - len(TRADE_SYMBOLS),
        "symbol_attrition_fraction": (len(PROPOSED_FUNDS) - len(TRADE_SYMBOLS))
        / len(PROPOSED_FUNDS),
        "date_attrition_vs_shared_calendar": date_attrition,
        "duplicate_symbol_dates": duplicates,
        "null_ohlc_rows": null_ohlc,
        "invalid_price_rows": invalid_prices,
        "by_symbol": json.loads(by_symbol.to_json(orient="records", date_format="iso")),
        "extreme_split_adjusted_overnight_gaps": sorted(
            gap_rows, key=lambda x: abs(x["overnight_gap"]), reverse=True
        )[:15],
        "failures": failures,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_cache.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    frame.to_parquet(output_cache, index=False)
    if failures:
        raise RuntimeError("readiness failed: " + "; ".join(failures))
    return result


@dataclass(frozen=True)
class RunConfig:
    trade_symbols: tuple[str, ...] = ("TQQQ", "SOXL")
    lookback: int = 20
    market_sma: int = 200
    holding_sessions: int = 5
    entry_delay_sessions: int = 1
    cost_bps_per_side: float = 5.0
    breadth: int = 1
    require_positive_momentum: bool = True
    require_market_trend: bool = True
    market_trend_direction: str = "above"
    require_all_funds_positive: bool = False
    require_qqq_positive_lookback: bool = False
    require_sma_rising: bool = False
    require_sma_falling: bool = False
    require_orderly_volatility: bool = False
    require_trend_efficiency: bool = False
    weight_scheme: str = "equal"
    volatility_scaling: str = "none"


def _max_drawdown_and_recovery(daily: pd.DataFrame) -> tuple[float, int, bool]:
    equity = 1.0 + daily["net_pnl"].cumsum()
    peak = equity.cummax()
    drawdown = (peak - equity) / peak
    max_dd = float(drawdown.max()) if len(drawdown) else 0.0
    max_recovery = 0
    unresolved = False
    peak_date = daily["date"].iloc[0] if len(daily) else None
    underwater_start = None
    for date, eq, pk in zip(daily["date"], equity, peak):
        if eq >= pk - 1e-12:
            if underwater_start is not None:
                max_recovery = max(max_recovery, int((date - underwater_start).days))
                underwater_start = None
            peak_date = date
        elif underwater_start is None:
            underwater_start = peak_date
    if underwater_start is not None and len(daily):
        unresolved = True
        max_recovery = max(max_recovery, int((daily["date"].iloc[-1] - underwater_start).days))
    return max_dd, max_recovery, unresolved


def simulate(
    frame: pd.DataFrame,
    config: RunConfig,
    allowed_decision_months: set[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    if frame["date"].max() > CUTOFF:
        raise RuntimeError("holdout check failed inside simulator")
    if int((frame["date"] >= HOLDOUT_START).sum()) != 0:
        raise RuntimeError("holdout rows detected inside simulator")
    if frame.duplicated(["symbol", "date"]).any():
        raise RuntimeError("duplicate symbol-date rows")

    opens = frame.pivot(index="date", columns="symbol", values="open").sort_index()
    closes = frame.pivot(index="date", columns="symbol", values="close").sort_index()
    dates = opens.index
    if set(REQUIRED_SYMBOLS) - set(opens.columns):
        raise RuntimeError("required symbols missing")
    if opens[REQUIRED_SYMBOLS].isna().any().any() or closes[REQUIRED_SYMBOLS].isna().any().any():
        raise RuntimeError("unaligned required-symbol data")

    trade_symbols = list(config.trade_symbols)
    if set(trade_symbols) - set(ALLOWED_ADAPTATION_SYMBOLS):
        raise RuntimeError(f"trade symbols outside frozen eligible set: {trade_symbols}")
    momentum = closes[trade_symbols] / closes[trade_symbols].shift(config.lookback) - 1.0
    qqq_sma = closes["QQQ"].rolling(config.market_sma, min_periods=config.market_sma).mean()
    if config.market_trend_direction == "above":
        qqq_trend = closes["QQQ"] > qqq_sma
    elif config.market_trend_direction == "below":
        qqq_trend = closes["QQQ"] < qqq_sma
    else:
        raise ValueError(f"unknown market trend direction: {config.market_trend_direction}")
    qqq_return = closes["QQQ"] / closes["QQQ"].shift(config.lookback) - 1.0
    qqq_sma_rising = qqq_sma > qqq_sma.shift(5)
    qqq_daily_return = closes["QQQ"].pct_change()
    qqq_volatility = qqq_daily_return.rolling(20, min_periods=20).std() * np.sqrt(252.0)
    qqq_volatility_median = qqq_volatility.shift(1).rolling(252, min_periods=126).median()
    orderly_volatility = qqq_volatility <= qqq_volatility_median
    qqq_efficiency = (
        (closes["QQQ"] / closes["QQQ"].shift(20) - 1.0).abs()
        / qqq_daily_return.abs().rolling(20, min_periods=20).sum()
    )
    qqq_efficiency_median = qqq_efficiency.shift(1).rolling(252, min_periods=126).median()
    efficient_trend = qqq_efficiency >= qqq_efficiency_median
    fund_volatility = closes[trade_symbols].pct_change().rolling(20, min_periods=20).std()

    daily = pd.DataFrame({"date": dates, "gross_pnl": 0.0, "cost": 0.0, "utilization": 0.0})
    trades: list[dict] = []
    first_decision = max(config.lookback, config.market_sma - 1)
    decision_i = first_decision
    while True:
        entry_i = decision_i + config.entry_delay_sessions
        exit_i = entry_i + config.holding_sessions
        if exit_i >= len(dates):
            break
        row = momentum.iloc[decision_i].dropna().sort_values(ascending=False)
        market_ok = bool(qqq_trend.iloc[decision_i]) if config.require_market_trend else True
        if config.require_all_funds_positive:
            values = momentum.iloc[decision_i][trade_symbols]
            market_ok = market_ok and bool(values.notna().all() and (values > 0).all())
        if config.require_qqq_positive_lookback:
            value = qqq_return.iloc[decision_i]
            market_ok = market_ok and bool(pd.notna(value) and value > 0)
        if config.require_sma_rising:
            value = qqq_sma_rising.iloc[decision_i]
            market_ok = market_ok and bool(pd.notna(value) and value)
        if config.require_sma_falling:
            value = qqq_sma < qqq_sma.shift(5)
            state = value.iloc[decision_i]
            market_ok = market_ok and bool(pd.notna(state) and state)
        if config.require_orderly_volatility:
            value = orderly_volatility.iloc[decision_i]
            market_ok = market_ok and bool(pd.notna(value) and value)
        if config.require_trend_efficiency:
            value = efficient_trend.iloc[decision_i]
            market_ok = market_ok and bool(pd.notna(value) and value)
        if allowed_decision_months is not None:
            market_ok = market_ok and str(dates[decision_i].to_period("M")) in allowed_decision_months
        if config.require_positive_momentum:
            row = row[row > 0]
        chosen = row.head(config.breadth).index.tolist() if market_ok else []
        if chosen:
            if config.weight_scheme == "equal":
                weights = {symbol: 1.0 / len(chosen) for symbol in chosen}
            elif config.weight_scheme == "inverse_vol":
                inverse = {
                    symbol: 1.0 / float(fund_volatility.iloc[decision_i][symbol])
                    for symbol in chosen
                    if pd.notna(fund_volatility.iloc[decision_i][symbol])
                    and fund_volatility.iloc[decision_i][symbol] > 0
                }
                if len(inverse) != len(chosen):
                    decision_i += 1
                    continue
                total_inverse = sum(inverse.values())
                weights = {symbol: value / total_inverse for symbol, value in inverse.items()}
            elif config.weight_scheme == "soxl_cap50":
                if chosen == ["SOXL"]:
                    weights = {"SOXL": 0.5}
                elif "SOXL" in chosen and "TQQQ" in chosen:
                    weights = {"SOXL": 0.5, "TQQQ": 0.5}
                else:
                    weights = {symbol: 1.0 / len(chosen) for symbol in chosen}
            else:
                raise ValueError(f"unknown weight scheme: {config.weight_scheme}")
            if config.volatility_scaling == "qqq_median_ratio":
                current_vol = qqq_volatility.iloc[decision_i]
                reference_vol = qqq_volatility_median.iloc[decision_i]
                scale = (
                    min(1.0, float(reference_vol / current_vol))
                    if pd.notna(current_vol) and pd.notna(reference_vol) and current_vol > 0
                    else 1.0
                )
            elif config.volatility_scaling == "none":
                scale = 1.0
            else:
                raise ValueError(f"unknown volatility scaling: {config.volatility_scaling}")
            weights = {symbol: weight * scale for symbol, weight in weights.items()}
            total_weight = sum(weights.values())
            entry_cost = config.cost_bps_per_side / 10_000.0
            exit_cost = config.cost_bps_per_side / 10_000.0
            daily.loc[entry_i, "cost"] += entry_cost * total_weight
            daily.loc[exit_i, "cost"] += exit_cost * total_weight
            daily.loc[entry_i:exit_i - 1, "utilization"] = total_weight
            gross_trade = 0.0
            for symbol in chosen:
                weight = weights[symbol]
                entry_px = float(opens.iloc[entry_i][symbol])
                exit_px = float(opens.iloc[exit_i][symbol])
                units_scaled = weight / entry_px
                gross_trade += weight * (exit_px / entry_px - 1.0)
                for i in range(entry_i + 1, exit_i + 1):
                    daily.loc[i, "gross_pnl"] += units_scaled * (
                        float(opens.iloc[i][symbol]) - float(opens.iloc[i - 1][symbol])
                    )
                trades.append(
                    {
                        "decision_date": dates[decision_i],
                        "entry_date": dates[entry_i],
                        "exit_date": dates[exit_i],
                        "symbol": symbol,
                        "weight": weight,
                        "signal": float(momentum.iloc[decision_i][symbol]),
                        "entry_price": entry_px,
                        "exit_price": exit_px,
                        "gross_return_contribution": weight * (exit_px / entry_px - 1.0),
                        "net_return_contribution": weight * (exit_px / entry_px - 1.0)
                        - weight * 2.0 * entry_cost,
                    }
                )
            decision_i = exit_i - config.entry_delay_sessions
        else:
            decision_i += 1

    daily["net_pnl"] = daily["gross_pnl"] - daily["cost"]
    daily["equity"] = 1.0 + daily["net_pnl"].cumsum()
    daily["qqq_open_return"] = opens["QQQ"].pct_change().fillna(0.0).to_numpy()
    trades_df = pd.DataFrame(trades)
    metrics = summarize(daily, trades_df, config)
    return daily, trades_df, metrics


def simulate_invalidation(
    frame: pd.DataFrame, config: RunConfig, exit_rule: str
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Hold at most config.holding_sessions; exit next open after causal invalidation."""
    if exit_rule not in {"market", "fund_momentum", "either"}:
        raise ValueError(f"unknown exit rule: {exit_rule}")
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    if frame["date"].max() > CUTOFF or int((frame["date"] >= HOLDOUT_START).sum()):
        raise RuntimeError("holdout check failed inside invalidation simulator")
    if frame.duplicated(["symbol", "date"]).any():
        raise RuntimeError("duplicate symbol-date rows")
    opens = frame.pivot(index="date", columns="symbol", values="open").sort_index()
    closes = frame.pivot(index="date", columns="symbol", values="close").sort_index()
    trade_symbols = list(config.trade_symbols)
    if set(trade_symbols) - set(ALLOWED_ADAPTATION_SYMBOLS):
        raise RuntimeError(f"trade symbols outside frozen eligible set: {trade_symbols}")
    dates = opens.index
    momentum = closes[trade_symbols] / closes[trade_symbols].shift(config.lookback) - 1.0
    qqq_sma = closes["QQQ"].rolling(config.market_sma, min_periods=config.market_sma).mean()
    qqq_trend = closes["QQQ"] > qqq_sma
    daily = pd.DataFrame({"date": dates, "gross_pnl": 0.0, "cost": 0.0, "utilization": 0.0})
    trades: list[dict] = []
    first_decision = max(config.lookback, config.market_sma - 1)
    decision_i = first_decision
    while True:
        entry_i = decision_i + config.entry_delay_sessions
        planned_exit_i = entry_i + config.holding_sessions
        if planned_exit_i >= len(dates):
            break
        ranked = momentum.iloc[decision_i].dropna().sort_values(ascending=False)
        if config.require_positive_momentum:
            ranked = ranked[ranked > 0]
        market_ok = bool(qqq_trend.iloc[decision_i]) if config.require_market_trend else True
        chosen = ranked.head(config.breadth).index.tolist() if market_ok else []
        if not chosen:
            decision_i += 1
            continue
        weight = 1.0 / len(chosen)
        side_cost = config.cost_bps_per_side / 10_000.0
        for symbol in chosen:
            actual_exit_i = planned_exit_i
            exit_reason = "max_holding"
            for close_i in range(entry_i, planned_exit_i):
                market_invalid = not bool(qqq_trend.iloc[close_i])
                fund_invalid = not bool(momentum.iloc[close_i][symbol] > 0)
                invalid = (
                    market_invalid
                    if exit_rule == "market"
                    else fund_invalid
                    if exit_rule == "fund_momentum"
                    else market_invalid or fund_invalid
                )
                if invalid:
                    actual_exit_i = close_i + 1
                    exit_reason = (
                        "market"
                        if market_invalid and not fund_invalid
                        else "fund_momentum"
                        if fund_invalid and not market_invalid
                        else "market_and_fund"
                    )
                    break
            entry_px = float(opens.iloc[entry_i][symbol])
            exit_px = float(opens.iloc[actual_exit_i][symbol])
            units_scaled = weight / entry_px
            daily.loc[entry_i, "cost"] += weight * side_cost
            daily.loc[actual_exit_i, "cost"] += weight * side_cost
            daily.loc[entry_i:actual_exit_i - 1, "utilization"] += weight
            for i in range(entry_i + 1, actual_exit_i + 1):
                daily.loc[i, "gross_pnl"] += units_scaled * (
                    float(opens.iloc[i][symbol]) - float(opens.iloc[i - 1][symbol])
                )
            gross = weight * (exit_px / entry_px - 1.0)
            trades.append({
                "decision_date": dates[decision_i],
                "entry_date": dates[entry_i],
                "planned_exit_date": dates[planned_exit_i],
                "exit_date": dates[actual_exit_i],
                "symbol": symbol,
                "weight": weight,
                "signal": float(momentum.iloc[decision_i][symbol]),
                "entry_price": entry_px,
                "exit_price": exit_px,
                "gross_return_contribution": gross,
                "net_return_contribution": gross - weight * 2.0 * side_cost,
                "exit_reason": exit_reason,
            })
        decision_i = planned_exit_i - config.entry_delay_sessions
    daily["net_pnl"] = daily["gross_pnl"] - daily["cost"]
    daily["equity"] = 1.0 + daily["net_pnl"].cumsum()
    daily["qqq_open_return"] = opens["QQQ"].pct_change().fillna(0.0).to_numpy()
    trades_df = pd.DataFrame(trades)
    metrics = summarize(daily, trades_df, config)
    metrics["exit_rule"] = exit_rule
    metrics["exit_reason_counts"] = (
        trades_df["exit_reason"].value_counts().to_dict() if len(trades_df) else {}
    )
    return daily, trades_df, metrics


def simulate_price_stop(
    frame: pd.DataFrame,
    config: RunConfig,
    stop_loss_fraction: float,
    stop_slippage_bps: float = 10.0,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Candidate-specific long stop: gap at open, otherwise stop less adverse slippage."""
    if not 0 < stop_loss_fraction < 1:
        raise ValueError("stop loss fraction must be between zero and one")
    if config.weight_scheme != "equal" or config.volatility_scaling != "none":
        raise ValueError("price-stop simulator supports the retained equal fixed sizing only")
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    if frame["date"].max() > CUTOFF or int((frame["date"] >= HOLDOUT_START).sum()):
        raise RuntimeError("holdout check failed inside price-stop simulator")
    opens = frame.pivot(index="date", columns="symbol", values="open").sort_index()
    lows = frame.pivot(index="date", columns="symbol", values="low").sort_index()
    closes = frame.pivot(index="date", columns="symbol", values="close").sort_index()
    dates = opens.index
    trade_symbols = list(config.trade_symbols)
    momentum = closes[trade_symbols] / closes[trade_symbols].shift(config.lookback) - 1
    sma = closes["QQQ"].rolling(config.market_sma, min_periods=config.market_sma).mean()
    market_ok_series = closes["QQQ"] > sma
    if config.require_sma_rising:
        market_ok_series &= sma > sma.shift(5)
    daily = pd.DataFrame({"date": dates, "gross_pnl": 0.0, "cost": 0.0, "utilization": 0.0})
    trades = []
    decision_i = max(config.lookback, config.market_sma - 1)
    side_cost = config.cost_bps_per_side / 10_000.0
    stop_slippage = stop_slippage_bps / 10_000.0
    while True:
        entry_i = decision_i + config.entry_delay_sessions
        planned_exit_i = entry_i + config.holding_sessions
        if planned_exit_i >= len(dates):
            break
        ranked = momentum.iloc[decision_i].dropna().sort_values(ascending=False)
        ranked = ranked[ranked > 0]
        chosen = ranked.head(config.breadth).index.tolist() if bool(market_ok_series.iloc[decision_i]) else []
        if not chosen:
            decision_i += 1
            continue
        weight = 1.0 / len(chosen)
        for symbol in chosen:
            entry_px = float(opens.iloc[entry_i][symbol])
            stop_px = entry_px * (1.0 - stop_loss_fraction)
            actual_exit_i = planned_exit_i
            fill_px = float(opens.iloc[planned_exit_i][symbol])
            exit_reason = "max_holding"
            for i in range(entry_i, planned_exit_i):
                open_px = float(opens.iloc[i][symbol])
                low_px = float(lows.iloc[i][symbol])
                if open_px <= stop_px:
                    actual_exit_i = i
                    fill_px = open_px
                    exit_reason = "gap_stop"
                    break
                if low_px <= stop_px:
                    actual_exit_i = i
                    fill_px = stop_px * (1.0 - stop_slippage)
                    exit_reason = "intraday_stop"
                    break
            daily.loc[entry_i, "cost"] += weight * side_cost
            daily.loc[actual_exit_i, "cost"] += weight * side_cost
            utilization_end = actual_exit_i if exit_reason == "intraday_stop" else actual_exit_i - 1
            if utilization_end >= entry_i:
                daily.loc[entry_i:utilization_end, "utilization"] += weight
            if actual_exit_i == entry_i:
                daily.loc[entry_i, "gross_pnl"] += weight * (fill_px / entry_px - 1.0)
            else:
                units = weight / entry_px
                for i in range(entry_i + 1, actual_exit_i + 1):
                    daily.loc[i, "gross_pnl"] += units * (
                        float(opens.iloc[i][symbol]) - float(opens.iloc[i - 1][symbol])
                    )
                if exit_reason == "intraday_stop":
                    daily.loc[actual_exit_i, "gross_pnl"] += units * (
                        fill_px - float(opens.iloc[actual_exit_i][symbol])
                    )
            gross = weight * (fill_px / entry_px - 1.0)
            trades.append({
                "decision_date": dates[decision_i],
                "entry_date": dates[entry_i],
                "planned_exit_date": dates[planned_exit_i],
                "exit_date": dates[actual_exit_i],
                "symbol": symbol,
                "weight": weight,
                "signal": float(momentum.iloc[decision_i][symbol]),
                "entry_price": entry_px,
                "exit_price": fill_px,
                "gross_return_contribution": gross,
                "net_return_contribution": gross - weight * 2 * side_cost,
                "exit_reason": exit_reason,
            })
        decision_i = planned_exit_i - config.entry_delay_sessions
    daily["net_pnl"] = daily["gross_pnl"] - daily["cost"]
    daily["equity"] = 1 + daily["net_pnl"].cumsum()
    daily["qqq_open_return"] = opens["QQQ"].pct_change().fillna(0).to_numpy()
    trades_df = pd.DataFrame(trades)
    metrics = summarize(daily, trades_df, config)
    metrics["stop_loss_fraction"] = stop_loss_fraction
    metrics["stop_slippage_bps"] = stop_slippage_bps
    metrics["exit_reason_counts"] = trades_df["exit_reason"].value_counts().to_dict()
    return daily, trades_df, metrics


def summarize(daily: pd.DataFrame, trades: pd.DataFrame, config: RunConfig) -> dict:
    monthly = daily.assign(month=daily["date"].dt.to_period("M")).groupby("month")["net_pnl"].sum()
    yearly = daily.assign(year=daily["date"].dt.year).groupby("year")["net_pnl"].sum()
    max_dd, recovery_days, unresolved = _max_drawdown_and_recovery(daily)
    active = daily["utilization"] > 0
    beta = float(
        np.cov(daily.loc[active, "net_pnl"], daily.loc[active, "qqq_open_return"], ddof=1)[0, 1]
        / np.var(daily.loc[active, "qqq_open_return"], ddof=1)
    ) if active.sum() > 2 and np.var(daily.loc[active, "qqq_open_return"], ddof=1) > 0 else 0.0
    turnover = float(2.0 * trades["weight"].sum()) if len(trades) else 0.0
    symbol_contrib = (
        trades.groupby("symbol")["net_return_contribution"].agg(["sum", "count"])
        .sort_values("sum", ascending=False)
        .reset_index()
        .to_dict(orient="records")
        if len(trades)
        else []
    )
    net_total = float(daily["net_pnl"].sum())
    top_share = (
        float(max(x["sum"] for x in symbol_contrib) / net_total)
        if symbol_contrib and net_total > 0
        else None
    )
    return {
        "configuration": asdict(config),
        "loaded_min_date": daily["date"].min().date().isoformat(),
        "loaded_max_date": daily["date"].max().date().isoformat(),
        "holdout_rows_loaded": int((daily["date"] >= HOLDOUT_START).sum()),
        "net_full_period_simple_return": net_total,
        "gross_full_period_simple_return": float(daily["gross_pnl"].sum()),
        "average_monthly_net_simple_return_all_loaded_months": float(monthly.mean()),
        "average_calendar_year_net_simple_return": float(yearly.mean()),
        "standard_max_drawdown": max_dd,
        "max_full_recovery_time_days": recovery_days,
        "ending_drawdown_unrecovered": unresolved,
        "negative_month_count_all_loaded": int((monthly < 0).sum()),
        "trade_legs": int(len(trades)),
        "independent_entry_decisions": int(trades["entry_date"].nunique()) if len(trades) else 0,
        "average_capital_utilization": float(daily["utilization"].mean()),
        "maximum_gross_exposure": float(daily["utilization"].max()),
        "gross_notional_turnover": turnover,
        "net_pnl_per_turnover": net_total / turnover if turnover else None,
        "market_beta_on_active_days": beta,
        "monthly": {str(k): float(v) for k, v in monthly.items()},
        "calendar_year": {str(k): float(v) for k, v in yearly.items()},
        "symbol_contribution": symbol_contrib,
        "top_symbol_net_contribution_share": top_share,
        "recent_windows": {
            label: summarize_window(daily, trades, start)
            for label, start in {
                "18m": "2024-11-01",
                "15m": "2025-02-01",
                "12m": "2025-05-01",
            }.items()
        },
    }


def summarize_window(
    daily: pd.DataFrame, trades: pd.DataFrame, start: str | pd.Timestamp
) -> dict:
    start_ts = pd.Timestamp(start)
    window = daily[daily["date"] >= start_ts].copy()
    window_trades = trades[trades["entry_date"] >= start_ts].copy() if len(trades) else trades
    monthly = (
        window.assign(month=window["date"].dt.to_period("M"))
        .groupby("month")["net_pnl"]
        .sum()
    )
    max_dd, recovery_days, unresolved = _max_drawdown_and_recovery(window)
    contributions = (
        window_trades.groupby("symbol")["net_return_contribution"].sum().to_dict()
        if len(window_trades)
        else {}
    )
    return {
        "start": start_ts.date().isoformat(),
        "end": window["date"].max().date().isoformat(),
        "net_simple_return": float(window["net_pnl"].sum()),
        "average_monthly_net_simple_return": float(monthly.mean()),
        "median_monthly_net_simple_return": float(monthly.median()),
        "negative_month_count": int((monthly < 0).sum()),
        "zero_month_count": int((monthly == 0).sum()),
        "standard_max_drawdown": max_dd,
        "max_full_recovery_time_days": recovery_days,
        "ending_drawdown_unrecovered": unresolved,
        "independent_entry_decisions": int(window_trades["entry_date"].nunique())
        if len(window_trades)
        else 0,
        "symbol_net_contribution": {str(k): float(v) for k, v in contributions.items()},
        "monthly": {str(k): float(v) for k, v in monthly.items()},
    }


def run_single(cache: Path, config: RunConfig, output_dir: Path) -> dict:
    frame = pd.read_parquet(cache)
    daily, trades, metrics = simulate(frame, config)
    output_dir.mkdir(parents=True, exist_ok=True)
    daily.to_csv(output_dir / "daily.csv", index=False)
    trades.to_csv(output_dir / "trades.csv", index=False)
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    ready = sub.add_parser("readiness")
    ready.add_argument("--catalog", type=Path, default=CATALOG)
    ready.add_argument("--output-json", type=Path, required=True)
    ready.add_argument("--output-cache", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--cache", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--lookback", type=int, default=20)
    run.add_argument("--market-sma", type=int, default=200)
    run.add_argument("--holding-sessions", type=int, default=5)
    run.add_argument("--entry-delay-sessions", type=int, default=1)
    run.add_argument("--cost-bps-per-side", type=float, default=5.0)
    run.add_argument("--breadth", type=int, default=1)
    args = parser.parse_args()
    if args.command == "readiness":
        print(json.dumps(readiness(args.catalog, args.output_json, args.output_cache), indent=2))
    else:
        config = RunConfig(
            lookback=args.lookback,
            market_sma=args.market_sma,
            holding_sessions=args.holding_sessions,
            entry_delay_sessions=args.entry_delay_sessions,
            cost_bps_per_side=args.cost_bps_per_side,
            breadth=args.breadth,
        )
        print(json.dumps(run_single(args.cache, config, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
