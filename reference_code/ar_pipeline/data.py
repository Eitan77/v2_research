from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

from .contracts import BarTiming, ContractError, timeframe_delta
from .paths import DATA_ROOT
from .validation import SafetyGateError, assert_safe_run_config


def connect_catalog(catalog_path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    path = Path(catalog_path) if catalog_path else DATA_ROOT / "catalog.duckdb"
    tmp = path.parent / ".duckdb_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=True, config={"temp_directory": str(tmp)})
    con.execute("set TimeZone='UTC'")
    return con


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(con.execute("select count(*) from information_schema.tables where table_name=?", [table]).fetchone()[0])


def validate_catalog(catalog_path: str | Path | None = None, full: bool = False) -> dict[str, Any]:
    con = connect_catalog(catalog_path)
    try:
        tables = [
            ("bars_1m", "timestamp"),
            ("bars_1d", "timestamp"),
            ("derived_bars_15m", "timestamp"),
            ("technical_indicators", "timestamp"),
            ("forward_labels", "timestamp"),
            ("research_matrix", "timestamp"),
            ("qqq_pit_membership_daily", "date"),
        ]
        out: dict[str, Any] = {"mode": "full" if full else "quick", "tables": {}, "lineage": {}, "warnings": []}
        for table, ts_col in tables:
            if not table_exists(con, table):
                out["tables"][table] = {"status": "missing"}
                out["warnings"].append(f"missing table/view: {table}")
                continue
            if full:
                row = con.execute(f"select count(*) as n, min({ts_col}), max({ts_col}) from {table}").fetchone()
                out["tables"][table] = {"status": "ok", "rows": int(row[0]), "min": str(row[1]), "max": str(row[2])}
            else:
                row = con.execute(f"select {ts_col} from {table} limit 1").fetchone()
                out["tables"][table] = {"status": "ok", "sample_timestamp": str(row[0]) if row else None}
        if full and table_exists(con, "research_matrix"):
            dup = con.execute(
                """
                select count(*) from (
                  select symbol,timestamp,timeframe,feed,adjustment,count(*) c
                  from research_matrix
                  group by 1,2,3,4,5
                  having c > 1
                )
                """
            ).fetchone()[0]
            out["research_matrix_duplicate_full_keys"] = int(dup)
        if full and table_exists(con, "bars_1m"):
            dup = con.execute(
                """
                select count(*) from (
                  select symbol,timestamp,timeframe,feed,adjustment,count(*) c
                  from bars_1m
                  group by 1,2,3,4,5
                  having c > 1
                )
                """
            ).fetchone()[0]
            out["bars_1m_duplicate_full_keys"] = int(dup)
            if dup:
                out["warnings"].append("bars_1m has duplicate full keys; loaders must deduplicate.")
        for table in ["derived_bars_5m", "derived_bars_10m", "derived_bars_15m", "derived_bars_30m", "derived_bars_1h", "derived_bars_4h"]:
            if not table_exists(con, table):
                continue
            columns = {row[0] for row in con.execute(f"describe {table}").fetchall()}
            required = {"bar_start_ts", "bar_end_ts", "available_at_ts", "bar_complete", "feed", "adjustment"}
            missing = sorted(required - columns)
            out["lineage"][table] = {"session_aligned_contract": not missing, "missing_columns": missing}
            if missing:
                out["warnings"].append(
                    f"{table} predates the session-aligned bar contract and is non-promotable until rebuilt: missing {missing}"
                )
        if table_exists(con, "qqq_pit_membership_daily"):
            membership_columns = {row[0] for row in con.execute("describe qqq_pit_membership_daily").fetchall()}
            required = {"effective_from", "effective_to", "known_at", "source_id"}
            missing = sorted(required - membership_columns)
            out["lineage"]["qqq_pit_membership_daily"] = {"strict_pit_contract": not missing, "missing_columns": missing}
            if missing:
                out["warnings"].append(
                    "qqq_pit_membership_daily is a download-universe approximation, not a strict PIT eligibility source; do not use it for promotion."
                )
        return out
    finally:
        con.close()


def load_research_matrix(config: dict[str, Any]) -> pd.DataFrame:
    data_cfg = config.get("data", {})
    scan = config.get("scan", {})
    table = data_cfg.get("table", "research_matrix")
    timeframe = scan.get("timeframe", "15m")
    start = scan.get("train_start")
    end = scan.get("train_end")
    features = scan.get("features", [])
    horizon = int(scan.get("horizon", 1))
    universe = scan.get("universe", "all")
    label_col = f"fwd_return_{horizon}"
    derived = _derived_feature_inputs(features)
    required = ["symbol", "timestamp", "timeframe", "open", "high", "low", "close", "volume", label_col, "fwd_mfe_" + str(horizon), "fwd_mae_" + str(horizon)]
    columns = list(dict.fromkeys(required + [f for f in features if f not in derived] + list(derived.values()) + ["is_qqq_member"]))
    con = connect_catalog(data_cfg.get("catalog_path"))
    try:
        available = {r[0] for r in con.execute(f"describe {table}").fetchall()}
        missing = [c for c in columns if c not in available]
        if missing:
            raise ValueError(f"{table} is missing required columns: {missing}")
        select_cols = ", ".join(columns)
        where = ["timeframe = ?"]
        params: list[Any] = [timeframe]
        if start:
            where.append("cast(timestamp as timestamp) >= ?")
            params.append(start)
        if end:
            where.append("cast(timestamp as timestamp) <= ?")
            params.append(end)
        if universe == "qqq_pit":
            where.append("coalesce(is_qqq_member, false)")
        if _session_name(scan) == "rth":
            where.extend(_rth_sql_predicates(horizon, timeframe))
        sql = f"""
            select {select_cols}
            from {table}
            where {' and '.join(where)}
              and {label_col} is not null
            qualify row_number() over (
              partition by symbol,timestamp,timeframe
              order by timestamp
            ) = 1
            order by timestamp, symbol
        """
        df = con.execute(sql, params).fetchdf()
    finally:
        con.close()
    if df.empty:
        raise ValueError("scan query returned no rows")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="mixed")
    df = apply_session_filter(df, scan, horizon=horizon)
    df = add_derived_features(df, features)
    if df.empty:
        raise ValueError("scan query returned no rows after session filtering")
    return df


def load_bar_screen_frame(config: dict[str, Any]) -> pd.DataFrame:
    """Load an as-of-safe cross-sectional screen frame.

    Unlike the legacy loader this function never uses a close-to-close forward
    label as an executable return.  A signal observes a completed bar, waits
    for its configured availability time, enters on a later actionable bar
    open, and exits on the close of a declared number of held bars.  The
    resulting ``bar_gross_return`` is still a screening estimate, but it is a
    causally valid one.
    """

    assert_safe_run_config(config)
    data_cfg = config["data"]
    scan = config["scan"]
    table = str(data_cfg["table"])
    features = [str(x) for x in scan.get("features", [])]
    if not features:
        raise SafetyGateError("scan.features must not be empty")
    derived = _derived_feature_inputs(features)
    timing = BarTiming(
        timeframe=str(scan["timeframe"]),
        timestamp_label=str(data_cfg["bar_timestamp_label"]),
        decision_latency_ms=int(scan.get("decision_latency_ms", 0)),
    )
    holding_bars = int(scan["holding_bars"])
    required = ["symbol", "timestamp", "timeframe", "open", "high", "low", "close", "volume", "feed", "adjustment"]
    columns = list(dict.fromkeys(required + [f for f in features if f not in derived] + list(derived.values()) + ["is_qqq_member"]))
    con = connect_catalog(data_cfg["catalog_path"])
    try:
        available = {row[0] for row in con.execute(f"describe {table}").fetchall()}
        missing = [column for column in columns if column not in available]
        if missing:
            raise SafetyGateError(f"{table} is missing required screen columns: {missing}")
        start = _inclusive_start(scan["train_start"])
        # Read enough future bars to form labels, then strictly retain only
        # decisions inside the training interval below.
        end_exclusive = _end_exclusive(scan["train_end"]) + pd.Timedelta(timing.delta) * (holding_bars + 2)
        where = ["timeframe = ?", "cast(timestamp as timestamptz) >= ?", "cast(timestamp as timestamptz) < ?", "feed = ?", "adjustment = ?"]
        params: list[Any] = [str(scan["timeframe"]), start.isoformat(), end_exclusive.isoformat(), data_cfg["feed"], data_cfg["adjustment"]]
        universe = data_cfg.get("universe", {})
        if universe.get("mode") == "pit_index":
            membership_view = str(universe.get("view", "qqq_pit_membership_daily"))
            if not table_exists(con, membership_view):
                raise SafetyGateError(f"PIT membership view is missing: {membership_view}")
            membership_columns = {row[0] for row in con.execute(f"describe {membership_view}").fetchall()}
            if not {"known_at", "source_id"}.issubset(membership_columns):
                raise SafetyGateError("PIT membership source lacks known_at/source_id provenance and cannot be used for promotion")
            where.append("coalesce(is_qqq_member, false)")
        revision_order = ["timestamp"]
        if "ingested_at" in available:
            revision_order.insert(0, "coalesce(try_cast(ingested_at as timestamp), cast(timestamp as timestamp)) desc")
        if "source_ingestion_id" in available:
            revision_order.append("coalesce(source_ingestion_id, '') desc")
        query = f"""
            select {', '.join(columns)}
            from {table}
            where {' and '.join(where)}
            qualify row_number() over (
              partition by symbol, timestamp, timeframe, feed, adjustment
              order by {', '.join(revision_order)}
            ) = 1
            order by symbol, cast(timestamp as timestamptz)
        """
        frame = con.execute(query, params).fetchdf()
    finally:
        con.close()
    if frame.empty:
        raise SafetyGateError("screen query returned no rows")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="mixed")
    frame = add_derived_features(frame, features)
    frame = _attach_causal_bar_outcomes(frame, timing, holding_bars)
    train_end = _end_exclusive(scan["train_end"])
    frame = frame[(frame["signal_ts"] >= start) & (frame["signal_ts"] < train_end)].copy()
    frame = apply_session_filter(frame, {**scan, "timeframe": scan["timeframe"], "horizon": holding_bars}, horizon=holding_bars)
    # apply_session_filter uses timestamp to describe the signal bar.  Keep
    # only rows whose actual entry/exit are still in the same exchange date.
    frame = frame[frame["entry_ts"].dt.tz_convert("America/New_York").dt.date.eq(frame["exit_ts"].dt.tz_convert("America/New_York").dt.date)]
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=features + ["bar_gross_return", "entry_ts", "exit_ts"])
    if frame.empty:
        raise SafetyGateError("no causally valid bar-screen rows remain after timing/session checks")
    return frame.sort_values(["signal_ts", "symbol"], kind="stable").reset_index(drop=True)


def cross_sectional_feature_ranks(frame: pd.DataFrame, features: list[str]) -> np.ndarray:
    """Percentile-rank features *within each decision timestamp only*.

    Ranking across the full history would leak future feature distributions into
    historical choices.  This operation is intentionally separated from the
    GPU tensor work so its timing semantics are obvious and testable.
    """

    if frame.empty:
        return np.empty((0, len(features)), dtype=np.float32)
    values = frame[features].replace([np.inf, -np.inf], np.nan)
    ranks = values.groupby(frame["signal_ts"], sort=False).rank(method="average", pct=True).fillna(0.5)
    return ((ranks.to_numpy(dtype=np.float32) - 0.5) * 2.0).astype(np.float32, copy=False)


def _attach_causal_bar_outcomes(frame: pd.DataFrame, timing: BarTiming, holding_bars: int) -> pd.DataFrame:
    out = frame.copy().sort_values(["symbol", "timestamp"], kind="stable").reset_index(drop=True)
    starts: list[pd.Timestamp] = []
    ends: list[pd.Timestamp] = []
    available: list[pd.Timestamp] = []
    entry_ts = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns, UTC]")
    exit_ts = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns, UTC]")
    entry_open = pd.Series(np.nan, index=out.index, dtype=float)
    exit_close = pd.Series(np.nan, index=out.index, dtype=float)
    for ts in out["timestamp"]:
        start, end = timing.bounds(ts)
        starts.append(start)
        ends.append(end)
        available.append(timing.available_at(ts))
    out["bar_start_ts"] = starts
    out["bar_end_ts"] = ends
    out["signal_ts"] = out["timestamp"]
    out["signal_available_ts"] = available
    for _, group in out.groupby("symbol", sort=False):
        index = group.index.to_numpy()
        start_ns = group["bar_start_ts"].astype("int64").to_numpy()
        availability_ns = group["signal_available_ts"].astype("int64").to_numpy()
        entry_positions = np.searchsorted(start_ns, availability_ns, side="left")
        exit_positions = entry_positions + holding_bars - 1
        valid = (entry_positions < len(group)) & (exit_positions < len(group))
        session = group["bar_start_ts"].dt.tz_convert("America/New_York").dt.date.to_numpy()
        valid &= session[np.minimum(entry_positions, len(group) - 1)] == session[np.minimum(exit_positions, len(group) - 1)]
        if not valid.any():
            continue
        loc = index[valid]
        entry_idx = entry_positions[valid]
        exit_idx = exit_positions[valid]
        entry_ts.loc[loc] = group["bar_start_ts"].iloc[entry_idx].to_numpy()
        exit_ts.loc[loc] = group["bar_end_ts"].iloc[exit_idx].to_numpy()
        entry_open.loc[loc] = pd.to_numeric(group["open"].iloc[entry_idx], errors="coerce").to_numpy()
        exit_close.loc[loc] = pd.to_numeric(group["close"].iloc[exit_idx], errors="coerce").to_numpy()
    out["entry_ts"] = entry_ts
    out["exit_ts"] = exit_ts
    out["entry_open"] = entry_open
    out["exit_close"] = exit_close
    out["bar_gross_return"] = out["exit_close"] / out["entry_open"] - 1.0
    return out


def _inclusive_start(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _end_exclusive(value: str) -> pd.Timestamp:
    raw = str(value)
    ts = pd.Timestamp(raw)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    ts = ts.tz_convert("UTC")
    # A date-only config is naturally inclusive for a human reader.  Make the
    # SQL endpoint exclusive so the final trading day is not silently dropped.
    if "T" not in raw and " " not in raw:
        return ts + pd.Timedelta(days=1)
    return ts


def _derived_feature_inputs(features: list[str]) -> dict[str, str]:
    base: dict[str, str] = {}
    for feature in features:
        if feature == "close_vs_vwap":
            base[feature] = "vwap"
        elif feature == "bar_return":
            base[feature] = "open"
        elif feature == "close_in_bar_range":
            base[feature] = "high"
        elif feature == "session_open_return":
            base[feature] = "open"
        elif feature == "minutes_since_open":
            base[feature] = "timestamp"
    if "close_in_bar_range" in features:
        base["close_in_bar_range_low"] = "low"
    return base


def add_derived_features(df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    if not features:
        return df
    out = df.copy()
    if "close_vs_vwap" in features:
        out["close_vs_vwap"] = out["close"].astype(float) / out["vwap"].replace(0, pd.NA).astype(float) - 1.0
    if "bar_return" in features:
        out["bar_return"] = out["close"].astype(float) / out["open"].replace(0, pd.NA).astype(float) - 1.0
    if "close_in_bar_range" in features:
        span = (out["high"].astype(float) - out["low"].astype(float)).replace(0, pd.NA)
        out["close_in_bar_range"] = (out["close"].astype(float) - out["low"].astype(float)) / span
    if "session_open_return" in features or "minutes_since_open" in features:
        local = pd.to_datetime(out["timestamp"], utc=True).dt.tz_convert("America/New_York")
        session_date = local.dt.strftime("%Y-%m-%d")
        sort_cols = ["symbol", "timestamp"]
        ordered = out.sort_values(sort_cols).copy()
        ordered["_session_date"] = session_date.loc[ordered.index].to_numpy()
        if "session_open_return" in features:
            session_open = ordered.groupby(["symbol", "_session_date"], sort=False)["open"].transform("first").replace(0, pd.NA)
            ordered["session_open_return"] = ordered["close"].astype(float) / session_open.astype(float) - 1.0
        if "minutes_since_open" in features:
            local_ordered = pd.to_datetime(ordered["timestamp"], utc=True).dt.tz_convert("America/New_York")
            ordered["minutes_since_open"] = (local_ordered.dt.hour * 60 + local_ordered.dt.minute) - (9 * 60 + 30)
        ordered = ordered.drop(columns=["_session_date"])
        for feature in ["session_open_return", "minutes_since_open"]:
            if feature in ordered:
                out[feature] = ordered[feature].reindex(out.index)
    return out


def apply_session_filter(df: pd.DataFrame, scan: dict[str, Any], horizon: int | None = None) -> pd.DataFrame:
    session = _session_name(scan)
    if session in {"all", "any", "extended", "none"}:
        return df
    if session not in {"rth", "regular", "regular_hours"}:
        raise ValueError(f"Unknown scan.session {session!r}; use 'rth' or 'all'")
    timeframe = str(scan.get("timeframe", "15m"))
    horizon = int(horizon if horizon is not None else scan.get("horizon", 1))
    minutes = _timeframe_minutes(timeframe)
    out = df.copy()
    ts_utc = pd.to_datetime(out["timestamp"], utc=True, format="mixed")
    exit_utc = ts_utc + pd.to_timedelta(horizon * minutes, unit="m")
    entry_et = ts_utc.dt.tz_convert("America/New_York")
    exit_et = exit_utc.dt.tz_convert("America/New_York")
    entry_date = entry_et.dt.normalize().dt.tz_localize(None)
    exit_date = exit_et.dt.normalize().dt.tz_localize(None)
    start = min(entry_date.min(), exit_date.min()) - pd.Timedelta(days=7)
    end = max(entry_date.max(), exit_date.max()) + pd.Timedelta(days=7)
    holidays = _market_holidays(start, end)
    is_trading_day = (
        entry_et.dt.weekday.lt(5)
        & exit_et.dt.weekday.lt(5)
        & ~entry_date.isin(holidays)
        & ~exit_date.isin(holidays)
    )
    entry_minutes = entry_et.dt.hour * 60 + entry_et.dt.minute
    exit_minutes = exit_et.dt.hour * 60 + exit_et.dt.minute
    rth_start = 9 * 60 + 30
    rth_end = 16 * 60
    same_session = entry_date.eq(exit_date)
    mask = is_trading_day & same_session & entry_minutes.ge(rth_start) & exit_minutes.le(rth_end)
    return out.loc[mask.to_numpy()].reset_index(drop=True)


def _session_name(scan: dict[str, Any]) -> str:
    return str(scan.get("session", scan.get("trading_session", "rth"))).lower()


def _rth_sql_predicates(horizon: int, timeframe: str) -> list[str]:
    minutes = horizon * _timeframe_minutes(timeframe)
    local_entry = "(cast(timestamp as timestamptz) at time zone 'America/New_York')"
    local_exit = f"((cast(timestamp as timestamptz) + interval '{minutes} minutes') at time zone 'America/New_York')"
    return [
        f"extract(dow from {local_entry}) between 1 and 5",
        f"extract(dow from {local_exit}) between 1 and 5",
        f"cast({local_entry} as date) = cast({local_exit} as date)",
        f"(extract(hour from {local_entry}) * 60 + extract(minute from {local_entry})) >= 570",
        f"(extract(hour from {local_exit}) * 60 + extract(minute from {local_exit})) <= 960",
    ]


def _timeframe_minutes(timeframe: str) -> int:
    if timeframe.endswith("m") and timeframe[:-1].isdigit():
        return int(timeframe[:-1])
    if timeframe.endswith("h") and timeframe[:-1].isdigit():
        return int(timeframe[:-1]) * 60
    raise ValueError(f"Unsupported timeframe for session filter: {timeframe!r}")


def _market_holidays(start: pd.Timestamp, end: pd.Timestamp) -> set[pd.Timestamp]:
    fed = set(USFederalHolidayCalendar().holidays(start=start, end=end).normalize())
    years = range(pd.Timestamp(start).year, pd.Timestamp(end).year + 1)
    market = set(fed)
    for year in years:
        market.add(_observed(pd.Timestamp(year=year, month=1, day=1)))
        market.add(_observed(pd.Timestamp(year=year, month=7, day=4)))
        market.add(_observed(pd.Timestamp(year=year, month=12, day=25)))
        market.add(_nth_weekday(year, 1, 0, 3))   # MLK Day
        market.add(_nth_weekday(year, 2, 0, 3))   # Presidents Day
        market.add(_last_weekday(year, 5, 0))     # Memorial Day
        if year >= 2022:
            market.add(_observed(pd.Timestamp(year=year, month=6, day=19)))
        market.add(_nth_weekday(year, 9, 0, 1))   # Labor Day
        market.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
        market.add(_easter_sunday(year) - pd.Timedelta(days=2))  # Good Friday
    return {pd.Timestamp(x).normalize() for x in market if start <= x <= end}


def _observed(day: pd.Timestamp) -> pd.Timestamp:
    if day.weekday() == 5:
        return day - pd.Timedelta(days=1)
    if day.weekday() == 6:
        return day + pd.Timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> pd.Timestamp:
    day = pd.Timestamp(year=year, month=month, day=1)
    offset = (weekday - day.weekday()) % 7
    return day + pd.Timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> pd.Timestamp:
    day = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
    offset = (day.weekday() - weekday) % 7
    return day - pd.Timedelta(days=offset)


def _easter_sunday(year: int) -> pd.Timestamp:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return pd.Timestamp(year=year, month=month, day=day)
