from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import pandas as pd

from cam0002 import choose_nonoverlapping_clusters, event_net_return, validate_cutoff
from run0001 import paths, schedule, summarize_variant


FORMATIONS = [10, 15, 30, 45, 60, 90, 120]
ABSOLUTE = [0.02, 0.04, 0.06]
RELATIVE = [6.0, 8.0, 10.0]
HOLDS = [5, 15, 30, 60]
COST_BPS = 10.0


def extract_horizon(
    con: duckdb.DuckDBPyConnection,
    formation: int,
) -> pd.DataFrame:
    query = f"""
    WITH ranked AS (
      SELECT b.*,
        row_number() OVER (
          PARTITION BY b.symbol,b.timestamp,b.timeframe,b.feed,b.adjustment
          ORDER BY coalesce(try_cast(b.ingested_at AS TIMESTAMP),TIMESTAMP '1900-01-01') DESC,
                   coalesce(b.source_ingestion_id,'') DESC
        ) rn
      FROM read_parquet(?,union_by_name=true,hive_partitioning=true) b
      WHERE b.date BETWEEN DATE '2024-08-01' AND DATE '2026-04-30'
        AND b.feed='sip' AND b.adjustment='raw'
    ), raw AS (
      SELECT symbol,date,try_cast(timestamp AS TIMESTAMPTZ) ts,open,close
      FROM ranked WHERE rn=1
    ), grid AS (
      SELECT c.symbol,c.date,c.eligible_liquid,s.expected_minutes,
             r.i AS minute_index,
             s.market_open + r.i * INTERVAL 1 MINUTE AS ts
      FROM coverage c JOIN schedule s USING(date),
           range(0,s.expected_minutes) r(i)
    ), joined AS (
      SELECT g.*,b.open AS raw_open,b.close AS raw_close
      FROM grid g LEFT JOIN raw b USING(symbol,date,ts)
    ), filled AS (
      SELECT *,
        last_value(raw_close IGNORE NULLS) OVER (
          PARTITION BY symbol,date ORDER BY minute_index
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS price
      FROM joined
    ), returns AS (
      SELECT *,
        price / lag(price,{formation}) OVER (
          PARTITION BY symbol,date ORDER BY minute_index
        ) - 1 AS formation_return,
        lead(coalesce(raw_open,price),1) OVER (
          PARTITION BY symbol,date ORDER BY minute_index
        ) AS entry_price,
        lead(coalesce(raw_open,price),6) OVER (
          PARTITION BY symbol,date ORDER BY minute_index
        ) AS exit5_price,
        lead(coalesce(raw_open,price),16) OVER (
          PARTITION BY symbol,date ORDER BY minute_index
        ) AS exit15_price,
        lead(coalesce(raw_open,price),31) OVER (
          PARTITION BY symbol,date ORDER BY minute_index
        ) AS exit30_price,
        lead(coalesce(raw_open,price),61) OVER (
          PARTITION BY symbol,date ORDER BY minute_index
        ) AS exit60_price
      FROM filled
    ), normal AS (
      SELECT *,
        avg(abs(formation_return)) OVER (
          PARTITION BY symbol,minute_index ORDER BY date
          ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING
        ) AS prior60_normal,
        count(formation_return) OVER (
          PARTITION BY symbol,minute_index ORDER BY date
          ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING
        ) AS prior_count
      FROM returns
    )
    SELECT symbol,date,ts AS event_ts,minute_index,expected_minutes,price,
           {formation} AS formation_minutes,formation_return,prior60_normal,
           entry_price,exit5_price,exit15_price,exit30_price,exit60_price
    FROM normal
    WHERE date >= DATE '2024-11-01'
      AND eligible_liquid AND price > 10
      AND minute_index >= {formation}
      AND minute_index <= expected_minutes - 62
      AND formation_return <= -0.02
      AND prior_count=60
      AND -formation_return >= 6 * prior60_normal
      AND entry_price IS NOT NULL AND exit60_price IS NOT NULL
    ORDER BY event_ts,symbol
    """
    out = con.execute(query, [paths()]).fetchdf()
    out["date"] = pd.to_datetime(out["date"])
    validate_cutoff(out)
    return out


def evaluate(events: pd.DataFrame, formation: int, absolute: float, relative: float, hold: int) -> dict:
    e = events[events["formation_minutes"] == formation].copy()
    e["shock"] = -e["formation_return"]
    e["surprise"] = e["shock"] / e["prior60_normal"]
    e = e[(e["shock"] >= absolute) & (e["surprise"] >= relative)]
    e = e.sort_values(["symbol", "date", "minute_index"]).groupby(
        ["symbol", "date"], as_index=False
    ).first()
    metrics, selected = summarize_variant(e, hold, COST_BPS)
    ex = selected[selected["date"] != pd.Timestamp("2025-04-07")]
    row = {
        "formation": formation, "absolute": absolute, "relative": relative,
        "hold": hold, "candidate_events": int(len(e)),
        "portfolio_events": metrics["events"], "portfolio_clusters": metrics["clusters"],
        "net": metrics["net"], "net_ex_20250407": float(ex["weighted_net"].sum()),
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
    p.add_argument("--coverage", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    temp = a.output_dir / "duckdb_tmp"
    temp.mkdir(exist_ok=True)
    coverage = pd.read_parquet(a.coverage)[["symbol", "date", "eligible_liquid"]]
    coverage["date"] = pd.to_datetime(coverage["date"])
    sched = schedule()
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{str(temp).replace(chr(92), '/')}'")
    con.register("coverage", coverage)
    con.register("schedule", sched)
    chunks = []
    for formation in FORMATIONS:
        print(f"extracting formation={formation}", flush=True)
        chunks.append(extract_horizon(con, formation))
    con.close()
    candidates = pd.concat(chunks, ignore_index=True)
    validate_cutoff(candidates)
    candidates.to_parquet(a.output_dir / "candidate_events.parquet", index=False)
    rows = [
        evaluate(candidates, formation, absolute, relative, hold)
        for formation in FORMATIONS
        for absolute in ABSOLUTE
        for relative in RELATIVE
        for hold in HOLDS
    ]
    if len(rows) != 252:
        raise RuntimeError("variant count mismatch")
    grid = pd.DataFrame(rows).sort_values("15m_avg_month", ascending=False)
    grid.to_csv(a.output_dir / "formation_grid.csv", index=False)
    leaders = grid.head(20).to_dict(orient="records")
    diagnostics = {
        "candidate_rows_before_threshold_specific_first_event": int(len(candidates)),
        "candidate_rows_by_formation": {
            str(k): int(v) for k, v in candidates.groupby("formation_minutes").size().items()
        },
        "positive_variants": int((grid["net"] > 0).sum()),
        "leaders_by_15m_avg_month": leaders,
    }
    (a.output_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    contract = {
        "executed_variant_count": 252, "expected_variant_count": 252,
        "formations": FORMATIONS, "absolute": ABSOLUTE, "relative": RELATIVE,
        "holds": HOLDS, "cost_bps_per_side": COST_BPS,
        "loaded_max_date": str(candidates["date"].max().date()) if len(candidates) else None,
        "holdout_rows_loaded": int((candidates["date"] >= "2026-05-01").sum()),
        "coverage_sha256": hashlib.sha256(a.coverage.read_bytes()).hexdigest(),
    }
    if contract["holdout_rows_loaded"] != 0:
        raise RuntimeError("holdout contamination")
    (a.output_dir / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(grid.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
