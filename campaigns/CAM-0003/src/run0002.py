from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import exchange_calendars as xcals
import pandas as pd

from cam0003 import max_drawdown_and_recovery, net_return, validate_cutoff
from readiness import paths, schedule


MORNINGS = [15, 30, 60]
HOLDS = [15, 30, 60]
COMPONENTS = ["combined_previous_close", "overnight_to_open", "open_session"]
EVAL_START = pd.Timestamp("2024-11-01")
CUTOFF = pd.Timestamp("2026-04-30")


def features(temp: Path) -> pd.DataFrame:
    sched = schedule()
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{str(temp).replace(chr(92), '/')}'")
    con.register("schedule", sched)
    q = """
    WITH ranked AS (
      SELECT b.*,
        row_number() OVER (
          PARTITION BY b.symbol,b.timestamp,b.timeframe,b.feed,b.adjustment
          ORDER BY coalesce(try_cast(b.ingested_at AS TIMESTAMP),TIMESTAMP '1900-01-01') DESC,
                   coalesce(b.source_ingestion_id,'') DESC
        ) rn
      FROM read_parquet(?,union_by_name=true,hive_partitioning=true) b
      WHERE b.date BETWEEN DATE '2024-10-01' AND DATE '2026-04-30'
        AND b.symbol='SPY' AND b.feed='sip' AND b.adjustment='raw'
    ), raw AS (
      SELECT date,try_cast(timestamp AS TIMESTAMPTZ) ts,open,close
      FROM ranked WHERE rn=1
    ), grid AS (
      SELECT s.date,s.expected_minutes,r.i AS minute_index,
             s.market_open+r.i*INTERVAL 1 MINUTE AS ts
      FROM schedule s,range(0,s.expected_minutes) r(i)
    ), joined AS (
      SELECT g.*,b.open AS raw_open,b.close AS raw_close
      FROM grid g LEFT JOIN raw b USING(date,ts)
    ), filled AS (
      SELECT *,
        last_value(raw_close IGNORE NULLS) OVER (
          PARTITION BY date ORDER BY minute_index
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS price
      FROM joined
    )
    SELECT date,max(expected_minutes) expected_minutes,
      max(CASE WHEN minute_index=0 THEN coalesce(raw_open,price) END) open_0930,
      max(CASE WHEN minute_index=14 THEN price END) close_m15,
      max(CASE WHEN minute_index=29 THEN price END) close_m30,
      max(CASE WHEN minute_index=59 THEN price END) close_m60,
      max(CASE WHEN minute_index=expected_minutes-15 THEN coalesce(raw_open,price) END) entry_h15,
      max(CASE WHEN minute_index=expected_minutes-30 THEN coalesce(raw_open,price) END) entry_h30,
      max(CASE WHEN minute_index=expected_minutes-60 THEN coalesce(raw_open,price) END) entry_h60,
      max(CASE WHEN minute_index=expected_minutes-30 THEN price END) pre_h15,
      max(CASE WHEN minute_index=expected_minutes-60 THEN price END) pre_h30,
      max(CASE WHEN minute_index=expected_minutes-120 THEN price END) pre_h60,
      max(CASE WHEN minute_index=expected_minutes-1 THEN price END) session_close
    FROM filled GROUP BY date ORDER BY date
    """
    d = con.execute(q, [paths()]).fetchdf()
    con.close()
    d["date"] = pd.to_datetime(d["date"])
    validate_cutoff(d)
    d["previous_close"] = d["session_close"].shift(1)
    return d[d["date"] >= EVAL_START].copy()


def summarize(d: pd.DataFrame, component: str, morning: int, hold: int, confirmation: str) -> dict:
    morning_close = d[f"close_m{morning}"]
    if component == "combined_previous_close":
        signal_return = morning_close/d["previous_close"]-1
    elif component == "overnight_to_open":
        signal_return = d["open_0930"]/d["previous_close"]-1
    else:
        signal_return = morning_close/d["open_0930"]-1
    active = signal_return > 0
    if confirmation != "none":
        pre_return = d[f"entry_h{hold}"]/d[f"pre_h{hold}"]-1
        active &= pre_return > 0
    pnl = pd.Series(0.0, index=d.index)
    pnl.loc[active] = [
        net_return(a, b, 2.0)
        for a, b in zip(d.loc[active, f"entry_h{hold}"], d.loc[active, "session_close"])
    ]
    calendar = pd.DataFrame({"date": pd.date_range(EVAL_START, CUTOFF, freq="D")})
    calendar["net_pnl"] = calendar["date"].map(dict(zip(d["date"], pnl))).fillna(0.0)
    dd, recovery, unresolved = max_drawdown_and_recovery(calendar)
    monthly = calendar.assign(month=calendar["date"].dt.to_period("M")).groupby("month")["net_pnl"].sum()
    row = {
        "component": component, "morning": morning, "hold": hold,
        "confirmation": confirmation, "signal_days": int(active.sum()),
        "net": float(pnl.sum()), "positive_fraction": float((pnl.loc[active] > 0).mean()),
        "max_drawdown": dd, "recovery_days": recovery, "unresolved": unresolved,
    }
    for label, start in [("18m", "2024-11-01"), ("15m", "2025-02-01"), ("12m", "2025-05-01")]:
        m = monthly[monthly.index >= pd.Period(start, "M")]
        row.update({
            f"{label}_net": float(m.sum()), f"{label}_avg_month": float(m.mean()),
            f"{label}_median_month": float(m.median()),
            f"{label}_negative_months": int((m < 0).sum()),
            f"{label}_zero_months": int((m == 0).sum()),
            f"{label}_trades": int((active & d["date"].ge(start)).sum()),
        })
    return row


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    temp = a.output_dir/"duckdb_tmp"; temp.mkdir(exist_ok=True)
    d = features(temp)
    required = [
        "previous_close", "open_0930", "close_m15", "close_m30", "close_m60",
        "entry_h15", "entry_h30", "entry_h60", "pre_h15", "pre_h30",
        "pre_h60", "session_close",
    ]
    nulls = {c: int(d[c].isna().sum()) for c in required}
    if any(nulls.values()):
        raise RuntimeError(f"feature attrition {nulls}")
    d.to_parquet(a.output_dir/"daily_features.parquet", index=False)
    rows = [
        summarize(d, component, morning, hold, confirmation)
        for component in COMPONENTS for morning in MORNINGS for hold in HOLDS
        for confirmation in ["none", "positive_preclose_same_length"]
    ]
    if len(rows) != 54: raise RuntimeError("variant mismatch")
    grid = pd.DataFrame(rows).sort_values("15m_avg_month", ascending=False)
    grid.to_csv(a.output_dir/"interval_grid.csv", index=False)
    diagnostics = {
        "sessions": len(d), "feature_nulls": nulls,
        "positive_variants": int((grid["net"] > 0).sum()),
        "leaders": grid.head(20).to_dict(orient="records"),
    }
    (a.output_dir/"diagnostics.json").write_text(json.dumps(diagnostics, indent=2)+"\n")
    contract = {
        "executed_variant_count": 54, "expected_variant_count": 54,
        "components": COMPONENTS, "mornings": MORNINGS, "holds": HOLDS,
        "confirmations": ["none", "positive_preclose_same_length"], "cost": 2,
        "loaded_max_date": str(d["date"].max().date()),
        "holdout_rows_loaded": int((d["date"] >= "2026-05-01").sum()),
    }
    (a.output_dir/"contract.json").write_text(json.dumps(contract, indent=2)+"\n")
    print(grid.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
