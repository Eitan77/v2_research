from __future__ import annotations

import re
from collections.abc import Sequence
from collections.abc import Iterable

import numpy as np
import pandas as pd


CUTOFF = pd.Timestamp("2026-04-30")
HOLDOUT_START = pd.Timestamp("2026-05-01")

_RESULT_PATTERN = re.compile(
    r"(?ix)"
    r"(?:\bq[1-4]\b|\bfy\d{2,4}\b)"
    r".{0,100}?"
    r"\b(?:(?:adj(?:usted)?|gaap|non-gaap)\.?\s*)?eps\b"
    r".{0,120}?"
    r"\b(?:beat(?:s)?|miss(?:es)?|inline|estimate|est\.?|vs\.?|reports?)\b"
    r"|"
    r"\b(?:(?:adj(?:usted)?|gaap|non-gaap)\.?\s*)?eps\b"
    r".{0,100}?"
    r"\b(?:beat(?:s)?|miss(?:es)?|inline|estimate|est\.?|vs\.?)\b"
    r".{0,120}?"
    r"(?:\bq[1-4]\b|\bfy\d{2,4}\b)"
    r"|"
    r"\bq[1-4]\b.{0,80}\bearnings\b.{0,100}\b(?:revenue|eps)\b"
)
_PREVIEW_PATTERN = re.compile(
    r"(?ix)"
    r"\bahead\s+of\b|\bpreview\b|\bexpected\s+to\b|\blikely\s+to\b|"
    r"\bgears?\s+up\b|\banalysts?\b|\bprice\s+over\s+earnings\b|"
    r"\bhow\s+to\s+earn\b|\bset\s+to\s+report\b|\bwill\s+report\b|"
    r"\bestimates?\s+bumped\b|\bpreps?\s+for\b|\bto\s+deliver\s+an?\s+eps\b|"
    r"\bupdates?\s+(?:fy|q[1-4])\b"
)


def is_earnings_release_headline(headline: str) -> bool:
    text = str(headline or "")
    return bool(_RESULT_PATTERN.search(text)) and not bool(
        _PREVIEW_PATTERN.search(text)
    )


def canonicalize_news_events(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse explicit release headlines into symbol event clusters."""
    data = frame.copy()
    data["created_at"] = pd.to_datetime(data["created_at"], utc=True)
    data = data[
        data["headline"].map(is_earnings_release_headline)
        & data["single_symbol"].fillna(False)
    ].sort_values(["symbol", "created_at"])
    rows = []
    for symbol, group in data.groupby("symbol"):
        cluster = -1
        previous = None
        for item in group.itertuples(index=False):
            if previous is None or item.created_at - previous > pd.Timedelta(hours=36):
                cluster += 1
                rows.append(
                    {
                        "symbol": symbol,
                        "event_timestamp": item.created_at,
                        "headline": item.headline,
                        "news_id": item.id,
                        "cluster": cluster,
                    }
                )
            previous = item.created_at
    return pd.DataFrame(rows)


def map_announcement_to_session(
    timestamp: pd.Timestamp, sessions: Sequence[pd.Timestamp]
) -> tuple[pd.Timestamp | None, str]:
    local = pd.Timestamp(timestamp)
    if local.tzinfo is None:
        raise ValueError("Announcement timestamp must be timezone aware")
    local = local.tz_convert("America/New_York")
    session_index = pd.DatetimeIndex(pd.to_datetime(list(sessions))).normalize()
    date = local.tz_localize(None).normalize()
    clock_minutes = local.hour * 60 + local.minute + local.second / 60.0
    if clock_minutes >= 16 * 60:
        candidates = session_index[session_index > date]
        bucket = "after_close"
    elif clock_minutes < 9 * 60 + 30:
        candidates = session_index[session_index >= date]
        bucket = "premarket"
    else:
        return None, "during_session_excluded"
    if len(candidates) == 0:
        return None, "unmapped_session"
    return pd.Timestamp(candidates[0]), bucket


def session_offset(
    entry_session: pd.Timestamp,
    sessions: Sequence[pd.Timestamp],
    offset: int,
) -> pd.Timestamp | None:
    index = pd.DatetimeIndex(pd.to_datetime(list(sessions))).normalize()
    matches = np.flatnonzero(index == pd.Timestamp(entry_session).normalize())
    if len(matches) != 1:
        return None
    target = int(matches[0]) + offset
    return None if target < 0 or target >= len(index) else pd.Timestamp(index[target])


def marketable_long_return(
    entry: float, exit_price: float, cost_bps_per_side: float
) -> float:
    return float(
        exit_price / entry - 1.0 - 2.0 * cost_bps_per_side / 10_000.0
    )


def protected_short_return(
    entry: float,
    planned_exit: float,
    path_highs: Iterable[float],
    stop_fraction: float,
    cost_bps_per_side: float,
    adverse_stop_slippage_bps: float = 10.0,
) -> tuple[float, bool, float]:
    stop_price = entry * (1.0 + stop_fraction)
    stopped = any(float(high) >= stop_price for high in path_highs)
    exit_price = (
        stop_price * (1.0 + adverse_stop_slippage_bps / 10_000.0)
        if stopped
        else planned_exit
    )
    pnl = (entry - exit_price) / entry - 2.0 * cost_bps_per_side / 10_000.0
    return float(pnl), stopped, float(exit_price)


def allocate_equal(returns: list[float]) -> float:
    return float(np.mean(returns)) if returns else 0.0


def max_drawdown_and_recovery(
    daily: pd.DataFrame, pnl_column: str = "net_pnl"
) -> tuple[float, int | None, bool]:
    frame = daily.sort_values("date").reset_index(drop=True).copy()
    if frame.empty:
        return 0.0, None, True
    equity = 1.0 + frame[pnl_column].cumsum()
    # The original normalized capital is an equity observation and therefore
    # the first running peak even when the first realized P&L is negative.
    peak = equity.cummax().clip(lower=1.0)
    drawdown = (peak - equity) / peak
    maximum_position = int(drawdown.to_numpy().argmax())
    maximum_drawdown = float(drawdown.iloc[maximum_position])
    if maximum_drawdown <= 1e-15:
        return 0.0, 0, False
    peak_value = float(peak.iloc[maximum_position])
    recovered = np.flatnonzero(
        (np.arange(len(frame)) > maximum_position)
        & (equity.to_numpy() >= peak_value)
    )
    if len(recovered) == 0:
        return maximum_drawdown, None, True
    recovery_index = int(recovered[0])
    days = (
        pd.Timestamp(frame.loc[recovery_index, "date"])
        - pd.Timestamp(frame.loc[maximum_position, "date"])
    ).days
    return maximum_drawdown, int(days), False
