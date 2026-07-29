from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cam0002 import (
    choose_nonoverlapping_clusters,
    event_net_return,
    max_drawdown_and_recovery,
    validate_cutoff,
)


ROOT = Path(r"D:\AlgoResearch\data\raw\alpaca\market\stocks\bars_1m\feed=sip")
EVAL_START = pd.Timestamp("2024-11-01")
CUTOFF = pd.Timestamp("2026-04-30")
DELAYS = [1, 2, 5]
HOLDS = [5, 10, 15, 20, 30, 45, 60]
COST_BPS = 10.0


def safe_paths(events: pd.DataFrame) -> list[str]:
    periods = sorted(pd.to_datetime(events["date"]).dt.to_period("M").unique())
    result = [
        str(ROOT / f"year={p.year}" / f"month={p.month:02d}" / "date=*" / "*.parquet").replace("\\", "/")
        for p in periods
    ]
    if any("2026/month=05" in p or "2026/month=06" in p for p in result):
        raise RuntimeError("holdout path requested")
    return result


def extract_paths(events: pd.DataFrame, temp: Path) -> pd.DataFrame:
    keys = events[["symbol", "date", "event_ts", "minute_index", "expected_minutes", "price"]].copy()
    keys["date"] = pd.to_datetime(keys["date"])
    validate_cutoff(keys)
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{str(temp).replace(chr(92), '/')}'")
    con.register("events", keys)
    query = """
    WITH ranked AS (
      SELECT b.symbol,b.date,try_cast(b.timestamp AS TIMESTAMPTZ) ts,b.open,b.close,
        row_number() OVER (
          PARTITION BY b.symbol,b.timestamp,b.timeframe,b.feed,b.adjustment
          ORDER BY coalesce(try_cast(b.ingested_at AS TIMESTAMP),TIMESTAMP '1900-01-01') DESC,
                   coalesce(b.source_ingestion_id,'') DESC
        ) rn
      FROM read_parquet(?,union_by_name=true,hive_partitioning=true) b
      JOIN (SELECT DISTINCT symbol,date FROM events) k
        ON b.symbol=k.symbol AND b.date=k.date
      WHERE b.date BETWEEN DATE '2024-11-01' AND DATE '2026-04-30'
        AND b.feed='sip' AND b.adjustment='raw'
    ), raw AS (
      SELECT symbol,date,ts,open,close FROM ranked WHERE rn=1
    ), grid AS (
      SELECT e.*,r.offset_min,
             e.event_ts + r.offset_min * INTERVAL 1 MINUTE AS path_ts
      FROM events e,range(0,66) r(offset_min)
      WHERE e.minute_index + r.offset_min < e.expected_minutes
    ), joined AS (
      SELECT g.*,b.open AS raw_open,b.close AS raw_close
      FROM grid g LEFT JOIN raw b
        ON g.symbol=b.symbol AND g.date=b.date AND g.path_ts=b.ts
    )
    SELECT *,
      coalesce(raw_open,
        last_value(raw_close IGNORE NULLS) OVER (
          PARTITION BY symbol,date,event_ts ORDER BY offset_min
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ),price) AS executable_price
    FROM joined
    ORDER BY event_ts,symbol,offset_min
    """
    result = con.execute(query, [safe_paths(events)]).fetchdf()
    con.close()
    validate_cutoff(result)
    return result


def monthly_metrics(selected: pd.DataFrame) -> tuple[dict, dict, float, int | None, bool]:
    pnl = selected.groupby("date")["weighted_net"].sum()
    daily = pd.DataFrame({"date": pd.date_range(EVAL_START, CUTOFF, freq="D")})
    daily["net_pnl"] = daily["date"].map(pnl).fillna(0.0)
    dd, recovery, unresolved = max_drawdown_and_recovery(daily)
    monthly = daily.assign(month=daily["date"].dt.to_period("M")).groupby("month")["net_pnl"].sum()
    windows = {}
    for label, start in [("18m", "2024-11-01"), ("15m", "2025-02-01"), ("12m", "2025-05-01")]:
        part = monthly[monthly.index >= pd.Period(start, "M")]
        windows[label] = {
            "net": float(part.sum()),
            "avg_month": float(part.mean()),
            "median_month": float(part.median()),
            "negative_months": int((part < 0).sum()),
            "zero_months": int((part == 0).sum()),
        }
    return windows, {str(k): float(v) for k, v in monthly.items()}, dd, recovery, unresolved


def evaluate(events: pd.DataFrame, paths: pd.DataFrame, delay: int, hold: int) -> tuple[dict, pd.DataFrame]:
    price_map = paths.pivot_table(
        index=["symbol", "date", "event_ts"], columns="offset_min",
        values="executable_price", aggfunc="last",
    )
    subset = events.merge(
        price_map[[delay, delay + hold]].rename(
            columns={delay: "entry_path_price", delay + hold: "exit_path_price"}
        ).reset_index(),
        on=["symbol", "date", "event_ts"], how="inner",
    )
    subset = subset.dropna(subset=["entry_path_price", "exit_path_price"]).copy()
    all_gross = subset["exit_path_price"] / subset["entry_path_price"] - 1
    all_net = np.array([
        event_net_return(a, b, COST_BPS)
        for a, b in zip(subset["entry_path_price"], subset["exit_path_price"])
    ])
    selected = choose_nonoverlapping_clusters(subset, delay + hold)
    selected["gross_return"] = selected["exit_path_price"] / selected["entry_path_price"] - 1
    selected["net_return"] = [
        event_net_return(a, b, COST_BPS)
        for a, b in zip(selected["entry_path_price"], selected["exit_path_price"])
    ]
    selected["weighted_net"] = selected["weight"] * selected["net_return"]
    windows, monthly, dd, recovery, unresolved = monthly_metrics(selected)
    ex = selected[selected["date"] != pd.Timestamp("2025-04-07")]
    result = {
        "delay": delay, "hold": hold, "candidate_events": int(len(subset)),
        "event_attrition": int(len(events) - len(subset)),
        "all_event_gross_mean": float(all_gross.mean()),
        "all_event_net_mean": float(all_net.mean()),
        "all_event_positive_net_fraction": float((all_net > 0).mean()),
        "portfolio_events": int(len(selected)),
        "portfolio_clusters": int(selected["event_ts"].nunique()),
        "portfolio_net": float(selected["weighted_net"].sum()),
        "portfolio_net_ex_20250407": float(ex["weighted_net"].sum()),
        "max_drawdown": dd, "recovery_days": recovery, "unresolved": unresolved,
        "windows": windows, "monthly": monthly,
    }
    return result, selected


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--events", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    temp = a.output_dir / "duckdb_tmp"
    temp.mkdir(exist_ok=True)
    events = pd.read_parquet(a.events)
    events["date"] = pd.to_datetime(events["date"])
    validate_cutoff(events)
    paths = extract_paths(events, temp)
    paths.to_parquet(a.output_dir / "path_prices.parquet", index=False)
    rows = []
    detailed = []
    for delay in DELAYS:
        for hold in HOLDS:
            metrics, _ = evaluate(events, paths, delay, hold)
            detailed.append(metrics)
            row = {k: v for k, v in metrics.items() if k not in {"windows", "monthly"}}
            for label, values in metrics["windows"].items():
                for key, value in values.items():
                    row[f"{label}_{key}"] = value
            rows.append(row)
    if len(rows) != 21:
        raise RuntimeError("variant count mismatch")
    grid = pd.DataFrame(rows).sort_values("15m_avg_month", ascending=False)
    grid.to_csv(a.output_dir / "path_grid.csv", index=False)
    diagnostics = {
        "source_event_count": int(len(events)),
        "path_rows": int(len(paths)),
        "variants": detailed,
        "best_by_15m_avg_month": grid.iloc[0].to_dict(),
    }
    (a.output_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    contract = {
        "executed_variant_count": 21, "expected_variant_count": 21,
        "source_events_sha256": sha256(a.events),
        "entry_delays": DELAYS, "holds": HOLDS, "cost_bps_per_side": COST_BPS,
        "loaded_max_date": str(paths["date"].max().date()),
        "holdout_rows_loaded": int((pd.to_datetime(paths["date"]) >= "2026-05-01").sum()),
        "price_rule": "raw minute open at offset; prior observed close/event price fallback",
    }
    if contract["holdout_rows_loaded"] != 0:
        raise RuntimeError("holdout contamination")
    (a.output_dir / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(grid.to_string(index=False))


if __name__ == "__main__":
    main()
