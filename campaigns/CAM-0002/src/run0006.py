from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import pandas as pd

from cam0002 import validate_cutoff
from run0001 import paths, schedule, summarize_variant


FORMATIONS = [10, 15, 30, 45, 60, 90, 120]
ABSOLUTE = [0.02, 0.04, 0.06]
RELATIVE = [6.0, 8.0, 10.0]
HOLDS = [5, 15, 30, 60]
COST_BPS = 10.0


def spy_panel(temp: Path) -> pd.DataFrame:
    sched = schedule()
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{str(temp).replace(chr(92), '/')}'")
    con.register("schedule", sched)
    query = """
    WITH ranked AS (
      SELECT b.*,
        row_number() OVER (
          PARTITION BY b.symbol,b.timestamp,b.timeframe,b.feed,b.adjustment
          ORDER BY coalesce(try_cast(b.ingested_at AS TIMESTAMP),TIMESTAMP '1900-01-01') DESC,
                   coalesce(b.source_ingestion_id,'') DESC
        ) rn
      FROM read_parquet(?,union_by_name=true,hive_partitioning=true) b
      WHERE b.date BETWEEN DATE '2024-08-01' AND DATE '2026-04-30'
        AND b.symbol='SPY' AND b.feed='sip' AND b.adjustment='raw'
    ), raw AS (
      SELECT date,try_cast(timestamp AS TIMESTAMPTZ) ts,close
      FROM ranked WHERE rn=1
    ), grid AS (
      SELECT s.date,r.i AS minute_index,
             s.market_open + r.i * INTERVAL 1 MINUTE AS ts
      FROM schedule s,range(0,s.expected_minutes) r(i)
    ), joined AS (
      SELECT g.*,b.close AS raw_close
      FROM grid g LEFT JOIN raw b USING(date,ts)
    )
    SELECT date,minute_index,
      last_value(raw_close IGNORE NULLS) OVER (
        PARTITION BY date ORDER BY minute_index
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
      ) AS spy_price
    FROM joined ORDER BY date,minute_index
    """
    out = con.execute(query, [paths()]).fetchdf()
    con.close()
    out["date"] = pd.to_datetime(out["date"])
    validate_cutoff(out)
    return out


def evaluate(events: pd.DataFrame, formation: int, absolute: float, relative: float, hold: int) -> dict:
    e = events[
        (events["formation_minutes"] == formation)
        & (events["residual_shock"] >= absolute)
        & (events["stock_surprise"] >= relative)
    ].copy()
    e = e.sort_values(["symbol", "date", "minute_index"]).groupby(
        ["symbol", "date"], as_index=False
    ).first()
    metrics, selected = summarize_variant(e, hold, COST_BPS)
    ex = selected[selected["date"] != pd.Timestamp("2025-04-07")]
    row = {
        "formation": formation, "residual_absolute": absolute,
        "stock_surprise_threshold": relative, "hold": hold,
        "candidate_events": int(len(e)), "portfolio_events": metrics["events"],
        "portfolio_clusters": metrics["clusters"], "net": metrics["net"],
        "net_ex_20250407": float(ex["weighted_net"].sum()),
        "max_drawdown": metrics["max_drawdown"],
        "recovery_days": metrics["recovery_days"], "unresolved": metrics["unresolved"],
        "positive_event_fraction": metrics["positive_event_fraction"],
    }
    for label, values in metrics["windows"].items():
        for key, value in values.items():
            row[f"{label}_{key}"] = value
    return row


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--candidates", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    temp = a.output_dir / "duckdb_tmp"
    temp.mkdir(exist_ok=True)
    events = pd.read_parquet(a.candidates)
    events["date"] = pd.to_datetime(events["date"])
    validate_cutoff(events)
    spy = spy_panel(temp)
    for formation in FORMATIONS:
        spy[f"spy_ret_{formation}"] = spy.groupby("date")["spy_price"].pct_change(
            formation, fill_method=None
        )
    long = spy.melt(
        id_vars=["date", "minute_index"],
        value_vars=[f"spy_ret_{f}" for f in FORMATIONS],
        var_name="formation_key", value_name="spy_formation_return",
    )
    long["formation_minutes"] = long["formation_key"].str.removeprefix("spy_ret_").astype(int)
    long = long.drop(columns="formation_key")
    before = len(events)
    events = events.merge(
        long, on=["date", "minute_index", "formation_minutes"], how="left",
        validate="many_to_one",
    )
    missing = int(events["spy_formation_return"].isna().sum())
    events = events.dropna(subset=["spy_formation_return"]).copy()
    events["residual_return"] = events["formation_return"] - events["spy_formation_return"]
    events["residual_shock"] = -events["residual_return"]
    events["stock_surprise"] = -events["formation_return"] / events["prior60_normal"]
    events.to_parquet(a.output_dir / "residual_events.parquet", index=False)
    rows = [
        evaluate(events, formation, absolute, relative, hold)
        for formation in FORMATIONS
        for absolute in ABSOLUTE
        for relative in RELATIVE
        for hold in HOLDS
    ]
    if len(rows) != 252:
        raise RuntimeError("variant count mismatch")
    grid = pd.DataFrame(rows).sort_values("15m_avg_month", ascending=False)
    grid.to_csv(a.output_dir / "residual_grid.csv", index=False)
    diagnostics = {
        "source_candidate_rows": before, "spy_missing_rows": missing,
        "residual_candidate_rows": int(len(events)),
        "positive_variants": int((grid["net"] > 0).sum()),
        "positive_ex_20250407_variants": int((grid["net_ex_20250407"] > 0).sum()),
        "leaders_by_15m_avg_month": grid.head(20).to_dict(orient="records"),
    }
    (a.output_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    contract = {
        "executed_variant_count": 252, "expected_variant_count": 252,
        "source_candidates_sha256": hashlib.sha256(a.candidates.read_bytes()).hexdigest(),
        "market_proxy": "SPY", "formations": FORMATIONS, "residual_absolute": ABSOLUTE,
        "stock_surprise": RELATIVE, "holds": HOLDS, "cost_bps_per_side": COST_BPS,
        "loaded_max_date": str(events["date"].max().date()) if len(events) else None,
        "holdout_rows_loaded": int((events["date"] >= "2026-05-01").sum()),
        "source_candidate_rows": before, "spy_missing_rows": missing,
    }
    if contract["holdout_rows_loaded"] != 0:
        raise RuntimeError("holdout contamination")
    (a.output_dir / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(grid.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
