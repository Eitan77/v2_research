from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import pandas as pd

from cam0002 import choose_nonoverlapping_clusters, event_net_return, validate_cutoff
from run0004 import monthly_metrics
from run0008 import safe_paths


SURPRISES = [6.0, 8.0, 10.0]
VOLUMES = [0.0, 2.0, 5.0]
BARRIERS = [None, 0.02, 0.04, 0.06]


def attach_high(paths: pd.DataFrame, temp: Path) -> tuple[pd.DataFrame, int]:
    keys = paths[["symbol", "date", "path_ts"]].drop_duplicates()
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{str(temp).replace(chr(92), '/')}'")
    con.register("keys", keys)
    query = """
    WITH ranked AS (
      SELECT b.symbol,b.date,try_cast(b.timestamp AS TIMESTAMPTZ) path_ts,b.high,
        row_number() OVER (
          PARTITION BY b.symbol,b.timestamp,b.timeframe,b.feed,b.adjustment
          ORDER BY coalesce(try_cast(b.ingested_at AS TIMESTAMP),TIMESTAMP '1900-01-01') DESC,
                   coalesce(b.source_ingestion_id,'') DESC
        ) rn
      FROM read_parquet(?,union_by_name=true,hive_partitioning=true) b
      JOIN (SELECT DISTINCT symbol,date FROM keys) k
        ON b.symbol=k.symbol AND b.date=k.date
      WHERE b.date BETWEEN DATE '2024-11-01' AND DATE '2026-04-30'
        AND b.feed='sip' AND b.adjustment='raw'
    )
    SELECT r.symbol,r.date,r.path_ts,r.high AS raw_high
    FROM ranked r JOIN keys k USING(symbol,date,path_ts)
    WHERE rn=1
    """
    high = con.execute(query, [safe_paths(paths)]).fetchdf()
    con.close()
    high["date"] = pd.to_datetime(high["date"])
    out = paths.merge(high, on=["symbol", "date", "path_ts"], how="left", validate="many_to_one")
    missing = int(out["raw_high"].isna().sum())
    out["minute_high"] = out[["raw_high", "raw_open", "raw_close", "minute_low"]].max(axis=1)
    return out, missing


def trade_from_path(group: pd.DataFrame, stop: float | None, target: float | None) -> dict:
    by = group.set_index("offset_min").sort_index()
    entry = float(by.loc[2, "executable_open"])
    stop_price = entry * (1-stop) if stop is not None else None
    target_price = entry * (1+target) if target is not None else None
    exit_price, reason, exit_offset = float(by.loc[62, "executable_open"]), "time", 62
    for offset in range(2, 62):
        row = by.loc[offset]
        op = float(row["executable_open"])
        low, high = float(row["minute_low"]), float(row["minute_high"])
        stop_hit = stop_price is not None and low <= stop_price
        target_hit = target_price is not None and high >= target_price
        if stop_hit:
            exit_price = min(op, stop_price)
            reason, exit_offset = "stop", offset
            break
        if target_hit:
            exit_price = max(op, target_price)
            reason, exit_offset = "target", offset
            break
    first = group.iloc[0]
    return {
        "symbol": first["symbol"], "date": first["date"], "event_ts": first["event_ts"],
        "minute_index": first["minute_index"], "entry_exec": entry,
        "exit_exec": exit_price, "exit_reason": reason, "exit_offset": exit_offset,
    }


def summarize(trades: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    if trades.empty:
        raise RuntimeError("unexpected empty trade family")
    selected = choose_nonoverlapping_clusters(trades, 62)
    selected["net_return"] = [
        event_net_return(a, b, 10.0) for a, b in zip(selected["entry_exec"], selected["exit_exec"])
    ]
    selected["weighted_net"] = selected["weight"] * selected["net_return"]
    windows, _, dd, recovery, unresolved = monthly_metrics(selected)
    return {
        "net": float(selected["weighted_net"].sum()), "events": len(selected),
        "clusters": selected["event_ts"].nunique(), "max_drawdown": dd,
        "recovery_days": recovery, "unresolved": unresolved, "windows": windows,
        "stops": int((selected["exit_reason"] == "stop").sum()),
        "targets": int((selected["exit_reason"] == "target").sum()),
        "time_exits": int((selected["exit_reason"] == "time").sum()),
        "median_exit_offset": float(selected["exit_offset"].median()),
    }, selected


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--paths", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    temp = a.output_dir / "duckdb_tmp"
    temp.mkdir(exist_ok=True)
    paths = pd.read_parquet(a.paths)
    paths["date"] = pd.to_datetime(paths["date"])
    validate_cutoff(paths)
    paths = paths[paths["anchor"].str.startswith("f15_a6_")].copy()
    paths, high_missing = attach_high(paths, temp)
    rows = []
    for surprise in SURPRISES:
        anchor = f"f15_a6_s{int(surprise)}"
        base = paths[paths["anchor"] == anchor]
        for volume in VOLUMES:
            filtered = base[base["volume_ratio"] >= volume]
            confirmed_groups = []
            for _, group in filtered.groupby(["symbol", "date", "event_ts"], sort=False):
                by = group.set_index("offset_min")
                if 62 in by.index and float(by.loc[1, "completed_close"]) > float(by.loc[0, "completed_close"]):
                    confirmed_groups.append(group)
            for stop in BARRIERS:
                for target in BARRIERS:
                    trades = pd.DataFrame([
                        trade_from_path(group, stop, target) for group in confirmed_groups
                    ])
                    metrics, selected = summarize(trades)
                    row = {
                        "stock_surprise": surprise, "volume_threshold": volume,
                        "stop": stop, "target": target,
                        "confirmed_events": len(confirmed_groups),
                        "portfolio_events": metrics["events"],
                        "portfolio_clusters": metrics["clusters"], "net": metrics["net"],
                        "max_drawdown": metrics["max_drawdown"],
                        "recovery_days": metrics["recovery_days"],
                        "unresolved": metrics["unresolved"], "stops": metrics["stops"],
                        "targets": metrics["targets"], "time_exits": metrics["time_exits"],
                        "median_exit_offset": metrics["median_exit_offset"],
                    }
                    for label, values in metrics["windows"].items():
                        for name, value in values.items():
                            row[f"{label}_{name}"] = value
                    rows.append(row)
    if len(rows) != 144:
        raise RuntimeError(f"variant mismatch {len(rows)}")
    grid = pd.DataFrame(rows).sort_values("15m_avg_month", ascending=False)
    grid.to_csv(a.output_dir / "risk_grid.csv", index=False)
    diagnostics = {
        "path_rows": len(paths), "raw_high_missing_rows_with_fallback": high_missing,
        "positive_variants": int((grid["net"] > 0).sum()),
        "leaders_by_15m_avg_month": grid.head(30).to_dict(orient="records"),
    }
    (a.output_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    contract = {
        "executed_variant_count": 144, "expected_variant_count": 144,
        "paths_sha256": hashlib.sha256(a.paths.read_bytes()).hexdigest(),
        "surprises": SURPRISES, "volume_thresholds": VOLUMES,
        "stops": BARRIERS, "targets": BARRIERS, "max_hold": 60,
        "intrabar_order": "adverse_stop_first", "raw_high_missing_rows": high_missing,
        "loaded_max_date": str(paths["date"].max().date()),
        "holdout_rows_loaded": int((paths["date"] >= "2026-05-01").sum()),
    }
    if contract["holdout_rows_loaded"]:
        raise RuntimeError("holdout contamination")
    (a.output_dir / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(grid.head(40).to_string(index=False))


if __name__ == "__main__":
    main()
