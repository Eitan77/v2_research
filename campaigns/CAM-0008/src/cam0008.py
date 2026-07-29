from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd


CUTOFF = pd.Timestamp("2026-04-30")
HOLDOUT_START = pd.Timestamp("2026-05-01")
POSITIVE_RATINGS = (
    "strong buy",
    "buy",
    "outperform",
    "overweight",
    "positive",
    "market outperform",
    "sector outperform",
    "accumulate",
)
NEGATIVE_RATINGS = (
    "sell",
    "underperform",
    "underweight",
    "negative",
    "reduce",
)
RATINGS = sorted(
    set(POSITIVE_RATINGS + NEGATIVE_RATINGS + (
        "hold",
        "neutral",
        "equal-weight",
        "equal weight",
        "market perform",
        "sector perform",
        "in-line",
        "inline",
    )),
    key=len,
    reverse=True,
)
RATING_PATTERN = "|".join(re.escape(value) for value in RATINGS)
RATING_CHANGE = re.compile(
    rf"(?i)\b(?P<verb>upgrades?|downgrades?)\s+"
    rf"(?P<subject>.{{2,120}}?)\s+to\s+(?P<rating>{RATING_PATTERN})\b"
)
INITIATION = re.compile(
    rf"(?i)\binitiates?\s+coverage\s+(?:on\s+)?"
    rf"(?P<subject>.{{2,120}}?)\s+with\s+(?P<rating>{RATING_PATTERN})\s+rating\b"
)
TARGET_ONLY = re.compile(
    r"(?i)\b(?:maintains?|reiterates?)\b.{2,160}?,\s*"
    r"(?P<verb>raises?|lowers?)\s+price\s+target\b"
)
EXCLUDE = re.compile(
    r"(?i)\btop\s+\d+\s+(?:upgrades?|downgrades?)\b|"
    r"\bhere\s+are\b|\bsoftware\s+upgrade\b|\bsystem\s+upgrade\b|"
    r"\bupgrades?\s+(?:its|the)\s+(?:network|platform|product|facility|system)\b|"
    r"\banalyst\s+(?:turns|is\s+no\s+longer)\b"
)


def normalize_firm(prefix: str) -> str:
    value = re.sub(r"(?i)^correction:\s*", "", str(prefix)).strip(" :-,")
    return re.sub(r"\s+", " ", value).lower()


def parse_action(headline: str) -> dict | None:
    text = str(headline or "").strip()
    if not text or EXCLUDE.search(text):
        return None
    change = RATING_CHANGE.search(text)
    if change:
        verb = change.group("verb").lower()
        action_type = "rating_upgrade" if verb.startswith("upgrade") else "rating_downgrade"
        return {
            "action_type": action_type,
            "action_sign": 1 if action_type == "rating_upgrade" else -1,
            "rating": change.group("rating").lower(),
            "firm": normalize_firm(text[: change.start()]),
        }
    initiation = INITIATION.search(text)
    if initiation:
        rating = initiation.group("rating").lower()
        if rating in POSITIVE_RATINGS:
            sign = 1
        elif rating in NEGATIVE_RATINGS:
            sign = -1
        else:
            return None
        return {
            "action_type": "positive_initiation" if sign > 0 else "negative_initiation",
            "action_sign": sign,
            "rating": rating,
            "firm": normalize_firm(text[: initiation.start()]),
        }
    target = TARGET_ONLY.search(text)
    if target:
        verb = target.group("verb").lower()
        sign = 1 if verb.startswith("raise") else -1
        return {
            "action_type": "target_raise" if sign > 0 else "target_lower",
            "action_sign": sign,
            "rating": None,
            "firm": normalize_firm(text[: target.start()]),
        }
    return None


def map_event_clock(
    timestamp: pd.Timestamp,
    sessions: Sequence[pd.Timestamp],
    session_closes: dict[pd.Timestamp, str] | None = None,
) -> dict:
    local = pd.Timestamp(timestamp)
    if local.tzinfo is None:
        raise ValueError("News timestamp must be timezone aware")
    local = local.tz_convert("America/New_York")
    index = pd.DatetimeIndex(pd.to_datetime(list(sessions))).normalize()
    date = local.tz_localize(None).normalize()
    minute = local.hour * 60 + local.minute + local.second / 60.0
    close_text = (
        session_closes.get(date, "16:00")
        if session_closes is not None
        else "16:00"
    )
    close_minute = int(close_text[:2]) * 60 + int(close_text[3:5])
    if minute >= close_minute:
        candidates = index[index > date]
        bucket = "after_close"
    elif minute < 9 * 60 + 30:
        candidates = index[index >= date]
        bucket = "premarket"
    elif minute <= close_minute - 15 and date in index:
        reaction_start = local.floor("min") + pd.Timedelta(minutes=1)
        entry_clock = reaction_start + pd.Timedelta(minutes=5)
        return {
            "entry_session": date,
            "release_bucket": "intraday",
            "reaction_start_minute": reaction_start.strftime("%H:%M"),
            "entry_minute": entry_clock.strftime("%H:%M"),
            "mapping_status": "actionable",
        }
    else:
        return {
            "entry_session": None,
            "release_bucket": "too_late",
            "reaction_start_minute": None,
            "entry_minute": None,
            "mapping_status": "too_late_excluded",
        }
    if len(candidates) == 0:
        return {
            "entry_session": None,
            "release_bucket": bucket,
            "reaction_start_minute": None,
            "entry_minute": None,
            "mapping_status": "unmapped_session",
        }
    return {
        "entry_session": pd.Timestamp(candidates[0]),
        "release_bucket": bucket,
        "reaction_start_minute": "09:30",
        "entry_minute": "09:35",
        "mapping_status": "actionable",
    }


def marketable_long_return(
    entry: float, exit_price: float, cost_bps_per_side: float
) -> float:
    return float(exit_price / entry - 1 - 2 * cost_bps_per_side / 10_000)


def equal_available_allocations(
    count: int, available: float, position_cap: float
) -> list[float]:
    if count <= 0 or available <= 0:
        return []
    size = min(position_cap, available / count)
    return [float(size)] * count


def protected_short_return(
    entry: float,
    planned_exit: float,
    path_highs: Iterable[float],
    stop_fraction: float,
    cost_bps_per_side: float,
    adverse_stop_slippage_bps: float = 10,
) -> tuple[float, bool, float]:
    stop = entry * (1 + stop_fraction)
    stopped = any(float(high) >= stop for high in path_highs)
    exit_price = (
        stop * (1 + adverse_stop_slippage_bps / 10_000)
        if stopped
        else planned_exit
    )
    pnl = (entry - exit_price) / entry - 2 * cost_bps_per_side / 10_000
    return float(pnl), stopped, float(exit_price)


def max_drawdown_and_recovery(
    daily: pd.DataFrame, pnl_column: str = "net_pnl"
) -> tuple[float, int | None, bool]:
    frame = daily.sort_values("date").reset_index(drop=True)
    if frame.empty:
        return 0.0, None, True
    equity = 1.0 + frame[pnl_column].cumsum()
    peak = equity.cummax().clip(lower=1.0)
    drawdown = (peak - equity) / peak
    maximum_position = int(drawdown.to_numpy().argmax())
    maximum = float(drawdown.iloc[maximum_position])
    if maximum <= 1e-15:
        return 0.0, 0, False
    peak_value = float(peak.iloc[maximum_position])
    recovered = np.flatnonzero(
        (np.arange(len(frame)) > maximum_position)
        & (equity.to_numpy() >= peak_value)
    )
    if len(recovered) == 0:
        return maximum, None, True
    recovery = int(recovered[0])
    days = (
        pd.Timestamp(frame.loc[recovery, "date"])
        - pd.Timestamp(frame.loc[maximum_position, "date"])
    ).days
    return maximum, int(days), False
