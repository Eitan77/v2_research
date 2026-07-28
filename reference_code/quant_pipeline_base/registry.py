from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    description: str
    family: str
    lookback: int | None = None
    classification: str = "time_series"  # time_series|cross_sectional|context|categorical
    cutoff: str = "completed_bar_available_at"
    calculation_frequency: str = "5m"
    price_basis: str = "raw_execution_price"
    dtype: str = "continuous"
    missing_rule: str = "exclude_pairwise"
    directional_meaning: str = "none"
    status: str = "requested"
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class TargetSpec:
    name: str
    description: str
    horizon_minutes: int | None
    entry_definition: str
    exit_definition: str
    classification: str = "raw"
    overlaps: bool = True


def target_registry(horizons: Iterable[int] | None = None) -> list[TargetSpec]:
    horizons = list(horizons) if horizons is not None else list(range(5, 390, 5))
    raw = [
        TargetSpec(f"fwd_return_{m}m", f"Raw return over next {m} minutes", m, "first bar open at/after decision availability", f"close after {m} minutes")
        for m in horizons
    ] + [TargetSpec("fwd_return_eod", "Raw return from next actionable open to RTH close", None, "first bar open at/after decision availability", "final complete RTH bar close")]
    adjusted = [TargetSpec(t.name+"_benchmark_adjusted", t.description+" minus benchmark return", t.horizon_minutes, t.entry_definition, t.exit_definition, "benchmark_adjusted", t.overlaps) for t in raw]
    return raw + adjusted


def feature_registry(lookbacks: Iterable[int], sector_available: bool = False, opening_windows: Iterable[int] | None = None) -> list[FeatureSpec]:
    """Registry is the scanner authority; construction never guesses by name."""
    out: list[FeatureSpec] = []
    def add(name: str, description: str, family: str, lb: int | None = None, cls: str = "time_series", direction: str = "none", status: str = "requested", reason: str | None = None) -> None:
        out.append(FeatureSpec(name, description, family, lb, cls, directional_meaning=direction, status=status, unavailable_reason=reason))
    rolling = {
        "return": ("simple return", "momentum"), "log_return": ("log return", "momentum"),
        "realized_vol": ("return standard deviation", "volatility"), "downside_vol": ("downside volatility", "volatility"),
        "upside_vol": ("upside volatility", "volatility"), "return_vol_ratio": ("return divided by volatility", "momentum"),
        "volume_sum": ("volume sum", "volume"), "volume_mean": ("average volume", "volume"),
        "relative_volume": ("current volume divided by rolling mean", "volume"), "dollar_volume_mean": ("average dollar volume", "volume"),
        "range_mean": ("average high-low range", "volatility"), "range_ratio": ("current range divided by rolling range", "volatility"),
        "return_consistency": ("share positive returns", "momentum"), "positive_return_sum": ("sum positive returns", "momentum"),
        "negative_return_sum": ("sum negative returns", "momentum"), "largest_positive_bar": ("largest positive return", "momentum"),
        "largest_negative_bar": ("largest negative return", "momentum"), "distance_rolling_high": ("distance from rolling high", "price_location"),
        "distance_rolling_low": ("distance from rolling low", "price_location"), "range_position": ("position in rolling range", "price_location"),
        "breakout_magnitude": ("close above prior rolling high", "momentum"), "breakdown_magnitude": ("close below prior rolling low", "momentum"),
        "volume_acceleration": ("short versus full-window volume", "volume"), "volatility_acceleration": ("short versus full-window volatility", "volatility"),
        "rolling_vwap_distance": ("distance from rolling VWAP", "vwap"), "volume_range_ratio": ("volume divided by range", "volume"),
        "return_volume_product": ("return multiplied by volume", "volume"), "return_outlier_score": ("return divided by recent volatility", "volatility"),
    }
    for n in lookbacks:
        for prefix, (desc, family) in rolling.items(): add(f"{prefix}_{n}", f"{n}-bar {desc}", family, n)
        for base in ["return", "relative_volume", "realized_vol", "range_position", "return_vol_ratio"]:
            add(f"{base}_rank_{n}", f"cross-sectional percentile rank of {base}_{n}", "cross_sectional", n, "cross_sectional")
    for name, desc, family in [
        ("return_since_open", "return from session opening price", "opening"), ("overnight_gap", "open versus prior session close", "opening"),
        ("previous_session_return", "previous session close-to-close return", "momentum"), ("bar_range_pct", "bar high-low range / close", "volatility"),
        ("body_to_range", "absolute body divided by range", "bar_structure"), ("upper_wick_to_range", "upper wick divided by range", "bar_structure"),
        ("lower_wick_to_range", "lower wick divided by range", "bar_structure"), ("close_location", "close location in bar", "bar_structure"),
        ("distance_session_vwap", "close / causal session VWAP - 1", "vwap"), ("distance_session_high", "close / session high - 1", "vwap"),
        ("distance_session_low", "close / session low - 1", "vwap"), ("minutes_since_open", "minutes elapsed since regular-session open", "calendar"),
        ("day_of_week", "exchange weekday", "calendar"),
        ("universe_breadth_positive", "share of universe with positive one-bar return", "breadth"),
        ("universe_return_dispersion", "cross-sectional return dispersion", "breadth"),
        ("bar_return", "current completed-bar return", "bar_structure"), ("bar_log_return", "current completed-bar log return", "bar_structure"),
        ("body_pct", "signed bar body divided by open", "bar_structure"), ("absolute_body_pct", "absolute body divided by open", "bar_structure"),
        ("inside_bar", "inside previous bar", "bar_structure"), ("outside_bar", "outside previous bar", "bar_structure"),
        ("higher_high", "high above prior high", "bar_structure"), ("higher_low", "low above prior low", "bar_structure"),
        ("lower_high", "high below prior high", "bar_structure"), ("lower_low", "low below prior low", "bar_structure"),
        ("session_range_position", "position within causal session range", "price_location"),
        ("cumulative_volume", "causal cumulative session volume", "volume"), ("current_volume_share", "current volume share of session volume", "volume"),
        ("vwap_slope", "one-bar change in causal session VWAP", "vwap"), ("vwap_cross", "crossing of causal session VWAP", "vwap"),
        ("consecutive_positive_bars", "positive-bar run length", "momentum"), ("consecutive_negative_bars", "negative-bar run length", "momentum"),
        ("month", "calendar month", "calendar"), ("quarter", "calendar quarter", "calendar"),
        ("minute_of_session", "regular-session minute", "calendar"), ("minutes_until_close", "minutes to regular close", "calendar"),
        ("market_up", "benchmark positive at decision", "market_context"), ("high_market_vol", "benchmark volatility above expanding median", "market_context"),
    ]: add(name, desc, family, cls="categorical" if name == "day_of_week" else "time_series")
    for minutes in (list(opening_windows) if opening_windows is not None else [1,3,5,10,15,20,30,45,60,90]):
        for base in ["opening_return", "opening_range", "opening_volume", "opening_realized_vol", "opening_close_location", "distance_opening_high", "distance_opening_low", "opening_breakout", "opening_breakdown"]:
            add(f"{base}_{minutes}m", f"{base.replace('_',' ')} after completed {minutes}-minute opening window", "opening", minutes)
    for lag in [1, 2, 3, 5, 10]:
        for base in ["lagged_market_return", "lagged_breadth_change", "lagged_dispersion_change", "unreacted_market_move"]:
            add(f"{base}_{lag}", f"{lag}-bar {base.replace('_',' ')}", "lead_lag", lag)
    for base in ["return_1", "relative_volume_10", "realized_vol_10", "range_position_10", "distance_session_vwap"]:
        for bars in [1560, 4680]:
            add(f"{base}_z_{bars}", f"point-in-time rolling z-score of {base}", "normalization", bars)
            # Ratios to a rolling mean are only defined for naturally positive
            # features. Signed returns and VWAP distances have means that cross
            # zero, which creates enormous, meaningless ratio spikes.
            if base in {"relative_volume_10", "realized_vol_10", "range_position_10"}:
                add(f"{base}_mean_ratio_{bars}", f"value divided by point-in-time rolling mean of {base}", "normalization", bars)
            add(f"{base}_median_diff_{bars}", f"value minus point-in-time rolling median of {base}", "normalization", bars)
    for sessions in [20,60]:
        add(f"tod_relative_volume_{sessions}", f"volume versus same session minute over prior {sessions} sessions", "volume", sessions)
        add(f"tod_cumulative_relative_volume_{sessions}", f"cumulative volume versus same session minute over prior {sessions} sessions", "volume", sessions)
    for sessions in [2,5]: add(f"session_return_{sessions}", f"return over prior {sessions} completed sessions", "momentum", sessions)
    for n in lookbacks:
        add(f"market_return_{n}", f"benchmark {n}-bar return", "market_context", n)
        add(f"stock_minus_market_return_{n}", f"stock minus benchmark {n}-bar return", "market_context", n)
    reason = "No point-in-time sector classification supplied" if not sector_available else None
    for name in ["sector_return", "stock_minus_sector_return", "sector_rank", "sector_breadth_positive"]:
        add(name, "Sector-dependent feature", "sector", status="requested" if sector_available else "unavailable", reason=reason)
    return out


def registry_frame(specs: Iterable[FeatureSpec | TargetSpec]):
    import pandas as pd
    return pd.DataFrame([asdict(x) for x in specs])
