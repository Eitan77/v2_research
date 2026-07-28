from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from .config import ScanConfig
from .registry import TargetSpec

ET = "America/New_York"


def load_canonical_bars(config: ScanConfig) -> pd.DataFrame:
    """Load only completed RTH bars.  No legacy feature/label/result tables."""
    if not config.allow_holdout_access and pd.Timestamp(config.discovery_end) >= pd.Timestamp(config.sealed_holdout_start):
        raise ValueError(f"Discovery end {config.discovery_end} reaches sealed holdout {config.sealed_holdout_start}")
    con = duckdb.connect(config.catalog_path, read_only=True)
    try:
        symbols = "" if not config.universe else "and symbol in (" + ",".join("?" for _ in config.universe) + ")"
        sql = f"""
          select symbol, bar_start_ts, bar_end_ts, available_at_ts, open, high, low, close, volume, vwap, session_date, ingested_at
          from {config.source_table}
          where feed=? and adjustment=? and bar_complete
            and cast(session_date as date) >= cast(? as date) and cast(session_date as date) <= cast(? as date)
            {symbols}
          order by symbol, bar_start_ts
        """
        params = [config.feed, config.adjustment, config.start, config.discovery_end, *config.universe]
        bars = con.execute(sql, params).fetchdf()
    finally: con.close()
    if bars.empty: raise ValueError("No canonical bars matched clean-room configuration")
    for col in ["bar_start_ts", "bar_end_ts", "available_at_ts"]: bars[col] = pd.to_datetime(bars[col], utc=True)
    bars["_ingested_at"] = pd.to_datetime(bars.pop("ingested_at"), utc=True, errors="coerce")
    bars = bars.sort_values(["symbol", "bar_start_ts", "_ingested_at"]).drop_duplicates(["symbol", "bar_start_ts"], keep="last").drop(columns="_ingested_at")
    bars["session_date"] = pd.to_datetime(bars["session_date"]).dt.date
    local = bars["bar_start_ts"].dt.tz_convert(ET)
    minute = local.dt.hour * 60 + local.dt.minute
    bars = bars[(local.dt.weekday < 5) & (minute >= 570) & (minute < 960)].copy()
    if config.decision_times_et:
        bars = bars[local.loc[bars.index].dt.strftime("%H:%M").isin(config.decision_times_et)].copy()
    return bars.reset_index(drop=True)


def add_targets(frame: pd.DataFrame, targets: list[TargetSpec], benchmark_symbol: str = "QQQ") -> pd.DataFrame:
    out = frame.sort_values(["symbol", "bar_start_ts"], kind="stable").copy()
    # A completed source bar becomes a decision at available_at_ts.  Entry is
    # strictly the following known bar's open; targets never begin at cutoff.
    out["decision_ts"] = out["available_at_ts"]
    out["entry_open"] = out.groupby(["symbol", "session_date"], sort=False)["open"].shift(-1)
    out["entry_ts"] = out.groupby(["symbol", "session_date"], sort=False)["bar_start_ts"].shift(-1)
    raw_values = {}
    for target in [t for t in targets if t.classification == "raw"]:
        if target.horizon_minutes is None:
            exit_close = out.groupby(["symbol", "session_date"], sort=False)["close"].transform("last")
        else:
            bars = max(1, target.horizon_minutes // 5)
            exit_close = out.groupby(["symbol", "session_date"], sort=False)["close"].shift(-bars)
        raw_values[target.name] = (exit_close / out["entry_open"] - 1.0).astype(np.float32)
    out = pd.concat([out, pd.DataFrame(raw_values, index=out.index)], axis=1)
    adjusted_values = {}
    for target in [t for t in targets if t.classification == "benchmark_adjusted"]:
        raw_name=target.name.removesuffix("_benchmark_adjusted")
        benchmark=out.loc[out.symbol.eq(benchmark_symbol),["decision_ts",raw_name]].drop_duplicates("decision_ts").set_index("decision_ts")[raw_name]
        adjusted_values[target.name]=(out[raw_name]-out.decision_ts.map(benchmark)).astype(np.float32)
    return pd.concat([out, pd.DataFrame(adjusted_values, index=out.index)], axis=1)


def validate_point_in_time(frame: pd.DataFrame, targets: list[TargetSpec]) -> dict[str, int]:
    required = {"symbol", "session_date", "decision_ts", "bar_start_ts", "bar_end_ts", "available_at_ts", "entry_ts"}
    missing = required - set(frame.columns)
    if missing: raise ValueError(f"Feature-table identifiers missing: {sorted(missing)}")
    duplicate = int(frame.duplicated(["symbol", "session_date", "decision_ts"]).sum())
    cutoff = int((frame["available_at_ts"] < frame["bar_end_ts"]).sum())
    target_before_entry = int((frame["entry_ts"] < frame["decision_ts"]).fillna(False).sum())
    if duplicate or cutoff or target_before_entry: raise ValueError(f"PIT validation failed duplicates={duplicate}, availability={cutoff}, target_timing={target_before_entry}")
    return {"rows": len(frame), "duplicate_identifiers": duplicate, "availability_violations": cutoff, "target_timing_violations": target_before_entry, "target_columns": len(targets)}
