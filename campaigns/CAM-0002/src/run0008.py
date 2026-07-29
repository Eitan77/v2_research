from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import pandas as pd

from cam0002 import (
    choose_nonoverlapping_clusters,
    event_net_return,
    validate_cutoff,
)
from run0004 import monthly_metrics
from run0007 import ANCHORS, anchor_events


ROOT = Path(r"D:\AlgoResearch\data\raw\alpaca\market\stocks\bars_1m\feed=sip")
MODES = {
    "immediate": 1,
    "reclaim2": 2,
    "reclaim5": 5,
    "no_new_low5": 5,
}
HOLDS = [5, 15, 30, 60]


def safe_paths(events: pd.DataFrame) -> list[str]:
    periods = sorted(events["date"].dt.to_period("M").unique())
    out = [
        str(ROOT / f"year={p.year}" / f"month={p.month:02d}" / "date=*" / "*.parquet").replace("\\", "/")
        for p in periods
    ]
    if any("2026/month=05" in x for x in out):
        raise RuntimeError("holdout path requested")
    return out


def extract_paths(events: pd.DataFrame, temp: Path) -> pd.DataFrame:
    keys = events[
        ["anchor", "symbol", "date", "event_ts", "minute_index",
         "expected_minutes", "price", "volume_ratio", "asset_class"]
    ].drop_duplicates().copy()
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{str(temp).replace(chr(92), '/')}'")
    con.register("events", keys)
    query = """
    WITH ranked AS (
      SELECT b.symbol,b.date,try_cast(b.timestamp AS TIMESTAMPTZ) ts,
             b.open,b.high,b.low,b.close,
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
      SELECT symbol,date,ts,open,high,low,close FROM ranked WHERE rn=1
    ), grid AS (
      SELECT e.*,r.offset_min,e.event_ts+r.offset_min*INTERVAL 1 MINUTE AS path_ts
      FROM events e,range(0,66) r(offset_min)
      WHERE e.minute_index+r.offset_min < e.expected_minutes
    ), joined AS (
      SELECT g.*,b.open AS raw_open,b.low AS raw_low,b.close AS raw_close
      FROM grid g LEFT JOIN raw b
        ON g.symbol=b.symbol AND g.date=b.date AND g.path_ts=b.ts
    ), filled AS (
      SELECT *,
        coalesce(raw_open,last_value(raw_close IGNORE NULLS) OVER (
          PARTITION BY anchor,symbol,date,event_ts ORDER BY offset_min
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ),price) AS executable_open,
        coalesce(raw_close,last_value(raw_close IGNORE NULLS) OVER (
          PARTITION BY anchor,symbol,date,event_ts ORDER BY offset_min
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ),price) AS completed_close
      FROM joined
    )
    SELECT *,coalesce(raw_low,completed_close) AS minute_low
    FROM filled ORDER BY anchor,event_ts,symbol,offset_min
    """
    out = con.execute(query, [safe_paths(keys)]).fetchdf()
    con.close()
    out["date"] = pd.to_datetime(out["date"])
    validate_cutoff(out)
    return out


def confirmation_ok(group: pd.DataFrame, mode: str) -> bool:
    by = group.set_index("offset_min")
    event_close = float(by.loc[0, "completed_close"])
    if mode == "immediate":
        return True
    if mode == "reclaim2":
        return float(by.loc[1, "completed_close"]) > event_close
    if mode == "reclaim5":
        return (
            float(by.loc[4, "completed_close"]) > event_close
            and float(by.loc[4, "completed_close"]) > float(by.loc[1, "completed_close"])
        )
    event_low = float(by.loc[0, "minute_low"])
    return (
        float(by.loc[4, "completed_close"]) > event_close
        and float(by.loc[by.index.isin([1, 2, 3, 4]), "minute_low"].min()) >= event_low
    )


def event_table(paths: pd.DataFrame, mode: str, hold: int) -> pd.DataFrame:
    delay = MODES[mode]
    rows = []
    for _, group in paths.groupby(["anchor", "symbol", "date", "event_ts"], sort=False):
        by = group.set_index("offset_min")
        if delay not in by.index or delay + hold not in by.index:
            continue
        if not confirmation_ok(group, mode):
            continue
        base = group.iloc[0]
        rows.append({
            "anchor": base["anchor"], "symbol": base["symbol"],
            "date": base["date"], "event_ts": base["event_ts"],
            "minute_index": base["minute_index"],
            "volume_ratio": base["volume_ratio"], "asset_class": base["asset_class"],
            "entry_exec": float(by.loc[delay, "executable_open"]),
            "exit_exec": float(by.loc[delay + hold, "executable_open"]),
        })
    return pd.DataFrame(rows)


def evaluate(events: pd.DataFrame, delay: int, hold: int) -> tuple[dict, pd.DataFrame]:
    if events.empty:
        windows = {
            k: {"net": 0.0, "avg_month": 0.0, "median_month": 0.0,
                "negative_months": 0, "zero_months": n}
            for k, n in [("18m", 18), ("15m", 15), ("12m", 12)]
        }
        return {
            "net": 0.0, "events": 0, "clusters": 0, "max_drawdown": 0.0,
            "recovery_days": 0, "unresolved": False, "windows": windows,
        }, pd.DataFrame(columns=["date", "weighted_net"])
    selected = choose_nonoverlapping_clusters(events, delay + hold)
    selected["net_return"] = [
        event_net_return(a, b, 10.0) for a, b in zip(selected["entry_exec"], selected["exit_exec"])
    ]
    selected["weighted_net"] = selected["weight"] * selected["net_return"]
    windows, _, dd, recovery, unresolved = monthly_metrics(selected)
    return {
        "net": float(selected["weighted_net"].sum()), "events": len(selected),
        "clusters": selected["event_ts"].nunique(), "max_drawdown": dd,
        "recovery_days": recovery, "unresolved": unresolved, "windows": windows,
    }, selected


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--events", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    temp = a.output_dir / "duckdb_tmp"
    temp.mkdir(exist_ok=True)
    source = pd.read_parquet(a.events)
    source["date"] = pd.to_datetime(source["date"])
    source["raw_shock"] = -source["formation_return"]
    validate_cutoff(source)
    pieces = []
    parent_counts = {}
    for anchor, spec in ANCHORS.items():
        e = anchor_events(source, spec).copy()
        e["anchor"] = anchor
        parent_counts[anchor] = len(e)
        pieces.append(e)
    anchors = pd.concat(pieces, ignore_index=True)
    paths = extract_paths(anchors, temp)
    paths.to_parquet(a.output_dir / "path_prices.parquet", index=False)
    rows = []
    for anchor in ANCHORS:
        ap = paths[paths["anchor"] == anchor]
        for volume_threshold in [0.0, 5.0]:
            for universe in ["all", "stocks"]:
                filtered_paths = ap[
                    (ap["volume_ratio"] >= volume_threshold)
                    & ((universe == "all") | (ap["asset_class"] == "stocks"))
                ]
                parent_events = filtered_paths[
                    ["symbol", "date", "event_ts"]
                ].drop_duplicates().shape[0]
                for mode, delay in MODES.items():
                    for hold in HOLDS:
                        eligible = event_table(filtered_paths, mode, hold)
                        metrics, selected = evaluate(eligible, delay, hold)
                        ex = selected[selected["date"] != pd.Timestamp("2025-04-07")]
                        row = {
                            "anchor": anchor, "volume_threshold": volume_threshold,
                            "universe": universe, "confirmation": mode,
                            "entry_delay": delay, "hold": hold,
                            "parent_events": parent_events,
                            "confirmed_events": len(eligible),
                            "confirmation_attrition": parent_events - len(eligible),
                            "portfolio_events": metrics["events"],
                            "portfolio_clusters": metrics["clusters"],
                            "net": metrics["net"],
                            "net_ex_20250407": float(ex["weighted_net"].sum()),
                            "max_drawdown": metrics["max_drawdown"],
                            "recovery_days": metrics["recovery_days"],
                            "unresolved": metrics["unresolved"],
                        }
                        for label, values in metrics["windows"].items():
                            for key, value in values.items():
                                row[f"{label}_{key}"] = value
                        rows.append(row)
    if len(rows) != 256:
        raise RuntimeError(f"variant mismatch {len(rows)}")
    grid = pd.DataFrame(rows).sort_values("15m_avg_month", ascending=False)
    grid.to_csv(a.output_dir / "stabilization_grid.csv", index=False)
    diagnostics = {
        "anchor_parent_events": parent_counts,
        "path_rows": len(paths), "positive_variants": int((grid["net"] > 0).sum()),
        "positive_ex_20250407_variants": int((grid["net_ex_20250407"] > 0).sum()),
        "leaders_by_15m_avg_month": grid.head(30).to_dict(orient="records"),
    }
    (a.output_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    contract = {
        "executed_variant_count": 256, "expected_variant_count": 256,
        "events_sha256": hashlib.sha256(a.events.read_bytes()).hexdigest(),
        "anchors": list(ANCHORS), "volume_thresholds": [0, 5],
        "universes": ["all", "stocks"], "confirmations": MODES,
        "holds": HOLDS, "loaded_max_date": str(paths["date"].max().date()),
        "holdout_rows_loaded": int((paths["date"] >= "2026-05-01").sum()),
    }
    if contract["holdout_rows_loaded"]:
        raise RuntimeError("holdout contamination")
    (a.output_dir / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(grid.head(40).to_string(index=False))


if __name__ == "__main__":
    main()
