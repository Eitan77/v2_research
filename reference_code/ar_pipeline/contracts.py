"""Explicit, testable contracts shared by every research engine.

The old project inferred important trading semantics from column names such as
``timestamp`` and ``fwd_return_4``.  That is not safe enough for research that
may eventually influence a live order.  This module makes the minimum timing
and provenance information first-class.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json
import re
from typing import Any, Literal

import pandas as pd


class ContractError(ValueError):
    """Raised when an artifact cannot be interpreted without an unsafe guess."""


TimestampLabel = Literal["start", "end"]
Side = Literal["long", "short"]


def timeframe_delta(value: str) -> timedelta:
    """Return the fixed duration represented by a supported research timeframe."""

    match = re.fullmatch(r"\s*(\d+)\s*([mhd])\s*", str(value).lower())
    if not match:
        raise ContractError(f"Unsupported fixed timeframe {value!r}; expected forms such as 1m, 15m, 1h, or 1d.")
    amount = int(match.group(1))
    if amount <= 0:
        raise ContractError("timeframe must be positive")
    unit = match.group(2)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


@dataclass(frozen=True)
class BarTiming:
    """How a vendor timestamps an OHLCV bar and when it is usable."""

    timeframe: str
    timestamp_label: TimestampLabel
    decision_latency_ms: int = 0

    def __post_init__(self) -> None:
        if self.timestamp_label not in {"start", "end"}:
            raise ContractError("bar timestamp_label must be 'start' or 'end'")
        if self.decision_latency_ms < 0:
            raise ContractError("decision_latency_ms must be >= 0")
        timeframe_delta(self.timeframe)

    @property
    def delta(self) -> timedelta:
        return timeframe_delta(self.timeframe)

    def bounds(self, timestamp: Any) -> tuple[pd.Timestamp, pd.Timestamp]:
        ts = as_utc(timestamp)
        if self.timestamp_label == "start":
            return ts, ts + self.delta
        return ts - self.delta, ts

    def available_at(self, timestamp: Any) -> pd.Timestamp:
        """Earliest legal decision timestamp for a completed bar."""

        _, end = self.bounds(timestamp)
        return end + pd.Timedelta(milliseconds=self.decision_latency_ms)


def as_utc(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        raise ContractError("timestamp may not be null")
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def canonical_json(value: Any) -> str:
    """Stable JSON used for reproducible config/artifact fingerprints."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def require_columns(frame: pd.DataFrame, columns: set[str], artifact: str) -> None:
    missing = sorted(columns - set(frame.columns))
    if missing:
        raise ContractError(f"{artifact} is missing required columns: {missing}")


def normalized_side(value: Any) -> Side:
    side = str(value).strip().lower()
    if side in {"long", "buy"}:
        return "long"
    if side in {"short", "sell_short", "sell"}:
        return "short"
    raise ContractError(f"Unsupported side {value!r}; expected long or short")
