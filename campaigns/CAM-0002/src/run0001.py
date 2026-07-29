from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import exchange_calendars as xcals
import numpy as np
import pandas as pd

from cam0002 import (
    choose_nonoverlapping_clusters,
    event_net_return,
    max_drawdown_and_recovery,
    validate_cutoff,
)


ROOT = Path(r"D:\AlgoResearch\data\raw\alpaca\market\stocks\bars_1m\feed=sip")
START = pd.Timestamp("2024-08-01")
CUTOFF = pd.Timestamp("2026-04-30")
EVAL_START = pd.Timestamp("2024-11-01")


def paths() -> list[str]:
    return [
        str(ROOT / f"year={p.year}" / f"month={p.month:02d}" / "date=*" / "*.parquet").replace("\\", "/")
        for p in pd.period_range(START, CUTOFF, freq="M")
    ]


def schedule() -> pd.DataFrame:
    cal = xcals.get_calendar("XNYS")
    rows = []
    for session in cal.sessions_in_range(START, CUTOFF):
        op, cl = cal.session_open(session), cal.session_close(session)
        rows.append({"date": session.tz_localize(None), "market_open": op,
                     "market_close": cl,
                     "expected_minutes": int((cl - op).total_seconds() // 60)})
    return pd.DataFrame(rows)


def extract_events(coverage_path: Path, temp: Path) -> pd.DataFrame:
    coverage = pd.read_parquet(coverage_path)[
        ["symbol", "date", "eligible_liquid"]
    ]
    coverage["date"] = pd.to_datetime(coverage["date"])
    sched = schedule()
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{str(temp).replace(chr(92), '/')}'")
    con.register("coverage", coverage)
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
        price / lag(price,60) OVER (PARTITION BY symbol,date ORDER BY minute_index) - 1 AS ret60,
        lead(coalesce(raw_open,price),1) OVER (PARTITION BY symbol,date ORDER BY minute_index) AS entry_price,
        lead(coalesce(raw_open,price),31) OVER (PARTITION BY symbol,date ORDER BY minute_index) AS exit30_price,
        lead(coalesce(raw_open,price),61) OVER (PARTITION BY symbol,date ORDER BY minute_index) AS exit60_price
      FROM filled
    ), normal AS (
      SELECT *,
        avg(abs(ret60)) OVER (
          PARTITION BY symbol,minute_index ORDER BY date
          ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING
        ) AS prior60_normal,
        count(ret60) OVER (
          PARTITION BY symbol,minute_index ORDER BY date
          ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING
        ) AS prior_count
      FROM returns
    ), candidates AS (
      SELECT *,
        row_number() OVER (PARTITION BY symbol,date ORDER BY minute_index) AS event_number
      FROM normal
      WHERE date >= DATE '2024-11-01'
        AND eligible_liquid
        AND price > 10
        AND minute_index >= 60
        AND minute_index <= expected_minutes - 62
        AND ret60 <= -0.04
        AND prior_count = 60
        AND -ret60 >= 8 * prior60_normal
        AND entry_price IS NOT NULL AND exit30_price IS NOT NULL AND exit60_price IS NOT NULL
    )
    SELECT symbol,date,ts AS event_ts,minute_index,expected_minutes,price,
           ret60,prior60_normal,entry_price,exit30_price,exit60_price
    FROM candidates WHERE event_number=1
    ORDER BY event_ts,symbol
    """
    events = con.execute(query, [paths()]).fetchdf()
    con.close()
    events["date"] = pd.to_datetime(events["date"])
    validate_cutoff(events)
    return events


def summarize_variant(events: pd.DataFrame, hold: int, cost: float) -> tuple[dict, pd.DataFrame]:
    selected = choose_nonoverlapping_clusters(events, hold)
    exit_col = f"exit{hold}_price"
    selected["gross_return"] = selected[exit_col] / selected["entry_price"] - 1
    selected["net_return"] = [
        event_net_return(a, b, cost) for a, b in zip(selected["entry_price"], selected[exit_col])
    ]
    selected["weighted_net"] = selected["weight"] * selected["net_return"]
    daily_pnl = selected.groupby("date")["weighted_net"].sum()
    dates = pd.date_range(EVAL_START, CUTOFF, freq="D")
    daily = pd.DataFrame({"date": dates})
    daily["net_pnl"] = daily["date"].map(daily_pnl).fillna(0.0)
    dd, recovery, unresolved = max_drawdown_and_recovery(daily)
    monthly = daily.assign(month=daily["date"].dt.to_period("M")).groupby("month")["net_pnl"].sum()
    windows = {}
    for label, start in [("18m", "2024-11-01"), ("15m", "2025-02-01"), ("12m", "2025-05-01")]:
        m = monthly[monthly.index >= pd.Period(start, "M")]
        windows[label] = {
            "net": float(m.sum()), "avg_month": float(m.mean()),
            "median_month": float(m.median()), "negative_months": int((m < 0).sum()),
            "zero_months": int((m == 0).sum()), "events": int(
                selected["date"].ge(start).sum()
            ),
        }
    metrics = {
        "hold_minutes": hold, "cost_bps_per_side": cost,
        "net": float(selected["weighted_net"].sum()),
        "events": int(len(selected)), "clusters": int(selected["event_ts"].nunique()),
        "max_drawdown": dd, "recovery_days": recovery, "unresolved": unresolved,
        "positive_event_fraction": float((selected["net_return"] > 0).mean()) if len(selected) else None,
        "windows": windows,
        "monthly": {str(k): float(v) for k, v in monthly.items()},
    }
    return metrics, selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    temp = args.output_dir / "duckdb_tmp"
    temp.mkdir(exist_ok=True)
    events = extract_events(
        Path("campaigns/CAM-0002/artifacts/readiness_session/daily_coverage.parquet"),
        temp,
    )
    events.to_parquet(args.output_dir / "events.parquet", index=False)
    variants, selected_outputs = [], {}
    for hold in [30, 60]:
        for cost in [10.0, 20.0, 40.0]:
            metrics, selected = summarize_variant(events, hold, cost)
            variants.append(metrics)
            selected_outputs[f"h{hold}_c{int(cost)}"] = selected
    if len(variants) != 6:
        raise RuntimeError("variant count mismatch")
    pd.json_normalize(variants).to_csv(args.output_dir / "variants.csv", index=False)
    selected_outputs["h60_c10"].to_parquet(args.output_dir / "selected_events.parquet", index=False)
    diagnostics = {
        "candidate_events_before_portfolio_nonoverlap": int(len(events)),
        "variants": variants,
        "loaded_max_date": str(events["date"].max().date()) if len(events) else None,
        "holdout_rows_loaded": int((events["date"] >= "2026-05-01").sum()),
    }
    (args.output_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    contract = {
        "executed_variant_count": 6, "expected_variant_count": 6,
        "event": "fixed 60-minute decline >4% and >8x causal same-clock normal",
        "entry": "next minute open", "holds": [30, 60], "costs": [10, 20, 40],
        "loaded_max_date": diagnostics["loaded_max_date"], "holdout_rows_loaded": 0,
    }
    (args.output_dir / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(json.dumps(diagnostics, indent=2))


if __name__ == "__main__":
    main()
