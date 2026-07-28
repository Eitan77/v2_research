"""Conservative bar-based execution estimates.

This engine is intentionally modest: OHLCV bars cannot prove intrabar order or
queue position.  It produces a fast, repeatable *screening* ledger and marks
every result as requiring quote-path validation before promotion.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

from ar_pipeline.contracts import BarTiming, ContractError, normalized_side, require_columns
from ar_pipeline.validation import SafetyGateError, validate_signal_ledger, validate_trade_ledger


@dataclass(frozen=True)
class BarFillPolicy:
    timing: BarTiming
    holding_bars: int
    slippage_bps_per_side: float = 0.0
    fee_bps_per_side: float = 0.0
    participation_rate: float = 0.05
    intrabar_ambiguity: Literal["worst_case", "reject"] = "worst_case"

    def __post_init__(self) -> None:
        if self.holding_bars <= 0:
            raise ContractError("holding_bars must be > 0")
        if self.slippage_bps_per_side < 0 or self.fee_bps_per_side < 0:
            raise ContractError("bar-fill costs must be non-negative")
        if not 0 < self.participation_rate <= 1:
            raise ContractError("participation_rate must be in (0, 1]")
        if self.intrabar_ambiguity not in {"worst_case", "reject"}:
            raise ContractError("intrabar_ambiguity must be worst_case or reject")

    @property
    def side_cost_rate(self) -> float:
        return (self.slippage_bps_per_side + self.fee_bps_per_side) / 10_000.0


def simulate_bar_fills(bars: pd.DataFrame, signals: pd.DataFrame, policy: BarFillPolicy) -> pd.DataFrame:
    """Turn completed-bar signals into a conservative, causally-valid trade ledger.

    Signals may only enter on the first *subsequent actionable* bar.  With a
    non-zero decision latency that means skipping an already-open next bar,
    because an OHLC bar cannot establish its price after the opening instant.
    """

    prepared_bars = _prepare_bars(bars, policy.timing)
    prepared_signals = validate_signal_ledger(signals, policy.timing)
    rows: list[dict[str, Any]] = []
    bar_groups = {str(symbol): group.reset_index(drop=True) for symbol, group in prepared_bars.groupby("symbol", sort=False)}
    for signal in prepared_signals.sort_values(["signal_available_ts", "symbol", "candidate_id"], kind="stable").itertuples(index=False):
        record = signal._asdict()
        group = bar_groups.get(str(record["symbol"]))
        if group is None or group.empty:
            rows.append(_unfilled_record(record, policy, "missing_symbol_bars"))
            continue
        rows.append(_fill_one(group, record, policy))
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    filled = out["bar_fill_status"].eq("filled")
    if filled.any():
        validate_trade_ledger(out.loc[filled].copy())
    return out


def _prepare_bars(bars: pd.DataFrame, timing: BarTiming) -> pd.DataFrame:
    require_columns(bars, {"symbol", "timestamp", "open", "high", "low", "close", "volume"}, "bar frame")
    out = bars.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce", format="mixed")
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    invalid = (
        out["timestamp"].isna()
        | (out[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (out["high"] < out[["open", "close", "low"]].max(axis=1))
        | (out["low"] > out[["open", "close", "high"]].min(axis=1))
        | (out["volume"] < 0)
    )
    if invalid.any():
        raise SafetyGateError(f"bar frame has {int(invalid.sum())} invalid OHLCV rows")
    if out.duplicated(["symbol", "timestamp"], keep=False).any():
        raise SafetyGateError("bar frame contains duplicate symbol/timestamp rows")
    bounds = [timing.bounds(ts) for ts in out["timestamp"]]
    out["bar_start_ts"] = [item[0] for item in bounds]
    out["bar_end_ts"] = [item[1] for item in bounds]
    out["session_date"] = out["bar_start_ts"].dt.tz_convert("America/New_York").dt.date
    return out.sort_values(["symbol", "bar_start_ts"], kind="stable").reset_index(drop=True)


def _fill_one(group: pd.DataFrame, signal: dict[str, Any], policy: BarFillPolicy) -> dict[str, Any]:
    available = pd.Timestamp(signal["signal_available_ts"])
    entry_idx = int(group["bar_start_ts"].searchsorted(available, side="left"))
    if entry_idx >= len(group):
        return _unfilled_record(signal, policy, "no_actionable_entry_bar")
    entry_bar = group.iloc[entry_idx]
    if entry_bar["bar_start_ts"] < available:
        return _unfilled_record(signal, policy, "entry_before_signal_available")
    if entry_bar["bar_start_ts"] - available > pd.Timedelta(policy.timing.delta):
        # Do not silently turn a missed/holiday/halts gap into a later trade.
        # A strategy that permits delayed entries must state that explicitly.
        return _unfilled_record(signal, policy, "no_immediate_actionable_entry_bar")
    if entry_idx + policy.holding_bars - 1 >= len(group):
        return _unfilled_record(signal, policy, "insufficient_future_bars")
    exit_idx = entry_idx + policy.holding_bars - 1
    exit_bar = group.iloc[exit_idx]
    if entry_bar["session_date"] != exit_bar["session_date"]:
        return _unfilled_record(signal, policy, "would_cross_session")
    quantity = float(signal.get("quantity", 1.0) or 1.0)
    if quantity <= 0:
        return _unfilled_record(signal, policy, "invalid_quantity")
    max_shares = float(entry_bar["volume"]) * policy.participation_rate
    if quantity > max_shares:
        return _unfilled_record(signal, policy, "participation_cap")

    side = normalized_side(signal["side"])
    entry_raw = float(entry_bar["open"])
    exit_raw, exit_reason, assumption = _resolve_exit(group.iloc[entry_idx : exit_idx + 1], signal, side, float(exit_bar["close"]), policy)
    if exit_raw is None:
        return _unfilled_record(signal, policy, exit_reason, assumption=assumption)
    entry_price = _apply_side_cost(entry_raw, side=side, is_entry=True, rate=policy.side_cost_rate)
    exit_price = _apply_side_cost(float(exit_raw), side=side, is_entry=False, rate=policy.side_cost_rate)
    gross_return = exit_raw / entry_raw - 1.0 if side == "long" else entry_raw / exit_raw - 1.0
    net_return = exit_price / entry_price - 1.0 if side == "long" else entry_price / exit_price - 1.0
    return {
        **signal,
        "side": side,
        "entry_submit_ts": available,
        "entry_ts": entry_bar["bar_start_ts"],
        "exit_ts": exit_bar["bar_end_ts"],
        "entry_ref_price": entry_raw,
        "exit_ref_price": float(exit_raw),
        "bar_entry_price": entry_price,
        "bar_exit_price": exit_price,
        "gross_bar_return": gross_return,
        "bar_return": net_return,
        "source_return": net_return,
        "bar_fill_status": "filled",
        "bar_exit_reason": exit_reason,
        "bar_fill_assumption": assumption,
        "bar_fill_requires_quote_path": True,
        "bar_fill_policy": str(asdict(policy)),
        "entry_bar_volume": float(entry_bar["volume"]),
        "participation_rate": policy.participation_rate,
    }


def _resolve_exit(
    path: pd.DataFrame,
    signal: dict[str, Any],
    side: str,
    default_close: float,
    policy: BarFillPolicy,
) -> tuple[float | None, str, str]:
    stop = _positive_number(signal.get("stop_price"))
    target = _positive_number(signal.get("take_profit_price"))
    if stop is None and target is None:
        return default_close, "time_exit", "next_bar_open_to_close_estimate"
    for bar in path.itertuples(index=False):
        low = float(bar.low)
        high = float(bar.high)
        opening = float(bar.open)
        if side == "long":
            stop_hit = stop is not None and low <= stop
            target_hit = target is not None and high >= target
        else:
            stop_hit = stop is not None and high >= stop
            target_hit = target is not None and low <= target
        if not (stop_hit or target_hit):
            continue
        if stop_hit and target_hit:
            if policy.intrabar_ambiguity == "reject":
                return None, "ambiguous_stop_target", "unresolved_intrabar_order"
            return _stop_fill(opening, float(stop), side), "ambiguous_stop_target_worst_case", "worst_case_intrabar_order"
        if stop_hit:
            return _stop_fill(opening, float(stop), side), "stop", "bar_touch_stop_estimate"
        return float(target), "take_profit", "bar_touch_target_estimate"
    return default_close, "time_exit", "next_bar_open_to_close_estimate"


def _positive_number(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    number = float(value)
    if number <= 0:
        raise SafetyGateError("stop_price and take_profit_price must be positive when provided")
    return number


def _stop_fill(opening: float, stop: float, side: str) -> float:
    return min(opening, stop) if side == "long" else max(opening, stop)


def _apply_side_cost(price: float, *, side: str, is_entry: bool, rate: float) -> float:
    buy = (side == "long" and is_entry) or (side == "short" and not is_entry)
    return price * (1.0 + rate if buy else 1.0 - rate)


def _unfilled_record(signal: dict[str, Any], policy: BarFillPolicy, reason: str, *, assumption: str = "not_filled") -> dict[str, Any]:
    return {
        **signal,
        "side": normalized_side(signal["side"]),
        "entry_submit_ts": signal["signal_available_ts"],
        "entry_ts": pd.NaT,
        "exit_ts": pd.NaT,
        "entry_ref_price": np.nan,
        "exit_ref_price": np.nan,
        "bar_entry_price": np.nan,
        "bar_exit_price": np.nan,
        "gross_bar_return": np.nan,
        "bar_return": np.nan,
        "source_return": np.nan,
        "bar_fill_status": "unfilled",
        "bar_exit_reason": reason,
        "bar_fill_assumption": assumption,
        "bar_fill_requires_quote_path": True,
        "bar_fill_policy": str(asdict(policy)),
        "entry_bar_volume": np.nan,
        "participation_rate": policy.participation_rate,
    }
