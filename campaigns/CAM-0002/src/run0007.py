from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import pandas as pd

from cam0002 import validate_cutoff
from run0001 import paths, schedule, summarize_variant


ANCHORS = {
    "source60": (60, "raw", 0.04, 8.0),
    "abrupt_residual15": (15, "residual", 0.06, 6.0),
    "slow_residual120": (120, "residual", 0.06, 8.0),
    "episode_control45": (45, "raw", 0.06, 10.0),
}
VOLUME_THRESHOLDS = [0.0, 2.0, 5.0, 10.0]
HOLDS = [5, 15, 30, 60]
UNLEVERED_ETFS = {
    "ARKK", "DIA", "GLD", "HYG", "IVV", "IWM", "LQD", "QQQ", "SMH",
    "SPY", "TLT", "USO", "VOO", "XLC", "XLE", "XLF", "XLI", "XLK",
    "XLP", "XLU", "XLV", "XLY",
}
LEVERAGED_ETFS = {"SOXL", "SOXS", "SQQQ", "TQQQ", "VIXY"}
ASSET_CLASSES = ["all", "stocks", "unlevered_etfs", "leveraged_inverse_vol_etfs"]


def summarize_allow_empty(events: pd.DataFrame, hold: int):
    if len(events):
        return summarize_variant(events, hold, 10.0)
    windows = {
        label: {
            "net": 0.0, "avg_month": 0.0, "median_month": 0.0,
            "negative_months": 0, "zero_months": months, "events": 0,
        }
        for label, months in [("18m", 18), ("15m", 15), ("12m", 12)]
    }
    metrics = {
        "events": 0, "clusters": 0, "net": 0.0, "max_drawdown": 0.0,
        "recovery_days": 0, "unresolved": False, "windows": windows,
    }
    return metrics, pd.DataFrame(columns=["date", "weighted_net"])


def volume_annotations(coverage_path: Path, keys: pd.DataFrame, temp: Path) -> pd.DataFrame:
    coverage = pd.read_parquet(coverage_path)[["symbol", "date"]]
    coverage["date"] = pd.to_datetime(coverage["date"])
    sched = schedule()
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{str(temp).replace(chr(92), '/')}'")
    con.register("coverage", coverage)
    con.register("schedule", sched)
    con.register("event_keys", keys[["symbol", "date", "minute_index"]].drop_duplicates())
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
      SELECT symbol,date,try_cast(timestamp AS TIMESTAMPTZ) ts,volume
      FROM ranked WHERE rn=1
    ), grid AS (
      SELECT c.symbol,c.date,r.i AS minute_index,
             s.market_open + r.i * INTERVAL 1 MINUTE AS ts
      FROM coverage c JOIN schedule s USING(date),
           range(0,s.expected_minutes) r(i)
    ), joined AS (
      SELECT g.*,coalesce(b.volume,0) AS minute_volume
      FROM grid g LEFT JOIN raw b USING(symbol,date,ts)
    ), normal AS (
      SELECT *,
        avg(minute_volume) OVER (
          PARTITION BY symbol,minute_index ORDER BY date
          ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING
        ) AS prior60_same_clock_volume,
        count(*) OVER (
          PARTITION BY symbol,minute_index ORDER BY date
          ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING
        ) AS prior_count
      FROM joined
    )
    SELECT n.symbol,n.date,n.minute_index,n.minute_volume,
           n.prior60_same_clock_volume,n.prior_count,
           n.minute_volume/nullif(n.prior60_same_clock_volume,0) AS volume_ratio
    FROM normal n JOIN event_keys k USING(symbol,date,minute_index)
    """
    out = con.execute(query, [paths()]).fetchdf()
    con.close()
    out["date"] = pd.to_datetime(out["date"])
    validate_cutoff(out)
    return out


def anchor_events(events: pd.DataFrame, spec: tuple[int, str, float, float]) -> pd.DataFrame:
    formation, kind, absolute, surprise = spec
    e = events[
        (events["formation_minutes"] == formation)
        & (events["stock_surprise"] >= surprise)
    ].copy()
    shock_col = "residual_shock" if kind == "residual" else "raw_shock"
    e = e[e[shock_col] >= absolute]
    return e.sort_values(["symbol", "date", "minute_index"]).groupby(
        ["symbol", "date"], as_index=False
    ).first()


def classify(symbol: str) -> str:
    if symbol in UNLEVERED_ETFS:
        return "unlevered_etfs"
    if symbol in LEVERAGED_ETFS:
        return "leveraged_inverse_vol_etfs"
    return "stocks"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--events", type=Path, required=True)
    p.add_argument("--coverage", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    temp = a.output_dir / "duckdb_tmp"
    temp.mkdir(exist_ok=True)
    events = pd.read_parquet(a.events)
    events["date"] = pd.to_datetime(events["date"])
    validate_cutoff(events)
    events["raw_shock"] = -events["formation_return"]
    annotations = volume_annotations(a.coverage, events, temp)
    before = len(events)
    events = events.merge(
        annotations, on=["symbol", "date", "minute_index"], how="left",
        validate="many_to_one",
    )
    missing = int(events["volume_ratio"].isna().sum())
    events["asset_class"] = events["symbol"].map(classify)
    events.to_parquet(a.output_dir / "annotated_events.parquet", index=False)
    rows = []
    anchor_counts = {}
    for anchor, spec in ANCHORS.items():
        base = anchor_events(events, spec)
        anchor_counts[anchor] = int(len(base))
        for volume_threshold in VOLUME_THRESHOLDS:
            volume_filtered = base[base["volume_ratio"] >= volume_threshold]
            for asset_class in ASSET_CLASSES:
                subset = (
                    volume_filtered if asset_class == "all"
                    else volume_filtered[volume_filtered["asset_class"] == asset_class]
                )
                for hold in HOLDS:
                    metrics, selected = summarize_allow_empty(subset, hold)
                    ex = selected[selected["date"] != pd.Timestamp("2025-04-07")]
                    row = {
                        "anchor": anchor, "volume_threshold": volume_threshold,
                        "asset_class": asset_class, "hold": hold,
                        "parent_events": len(base), "filtered_events": len(subset),
                        "attrition": len(base) - len(subset),
                        "portfolio_events": metrics["events"],
                        "portfolio_clusters": metrics["clusters"], "net": metrics["net"],
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
        raise RuntimeError(f"variant count mismatch: {len(rows)}")
    grid = pd.DataFrame(rows).sort_values("15m_avg_month", ascending=False)
    grid.to_csv(a.output_dir / "volume_universe_grid.csv", index=False)
    diagnostics = {
        "source_candidate_rows": before, "volume_annotation_missing_rows": missing,
        "anchor_parent_events": anchor_counts,
        "positive_variants": int((grid["net"] > 0).sum()),
        "positive_ex_20250407_variants": int((grid["net_ex_20250407"] > 0).sum()),
        "leaders_by_15m_avg_month": grid.head(30).to_dict(orient="records"),
    }
    (a.output_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    contract = {
        "executed_variant_count": 256, "expected_variant_count": 256,
        "events_sha256": hashlib.sha256(a.events.read_bytes()).hexdigest(),
        "coverage_sha256": hashlib.sha256(a.coverage.read_bytes()).hexdigest(),
        "anchors": list(ANCHORS), "volume_thresholds": VOLUME_THRESHOLDS,
        "asset_classes": ASSET_CLASSES, "holds": HOLDS,
        "loaded_max_date": str(events["date"].max().date()),
        "holdout_rows_loaded": int((events["date"] >= "2026-05-01").sum()),
        "volume_annotation_missing_rows": missing,
    }
    if contract["holdout_rows_loaded"] or missing:
        raise RuntimeError("integrity/attrition gate failed")
    (a.output_dir / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(grid.head(40).to_string(index=False))


if __name__ == "__main__":
    main()
