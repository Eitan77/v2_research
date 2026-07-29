from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from cam0002 import validate_cutoff
from run0008 import MODES, evaluate, event_table, extract_paths


FORMATIONS = [10, 15, 30]
ABSOLUTE = [0.02, 0.04, 0.06]
SURPRISE = [6.0, 8.0, 10.0]
VOLUME = [0.0, 2.0, 5.0]
CONFIRMATIONS = ["reclaim2", "no_new_low5"]
HOLDS = [15, 30, 60]


def select_first(source: pd.DataFrame, formation: int, absolute: float, surprise: float) -> pd.DataFrame:
    e = source[
        (source["formation_minutes"] == formation)
        & (source["residual_shock"] >= absolute)
        & (source["stock_surprise"] >= surprise)
        & (source["asset_class"] == "stocks")
    ].copy()
    return e.sort_values(["symbol", "date", "minute_index"]).groupby(
        ["symbol", "date"], as_index=False
    ).first()


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
    validate_cutoff(source)
    pieces, parent_counts = [], {}
    for formation in FORMATIONS:
        for absolute in ABSOLUTE:
            for surprise in SURPRISE:
                key = f"f{formation}_a{int(absolute*100)}_s{int(surprise)}"
                e = select_first(source, formation, absolute, surprise)
                e["anchor"] = key
                pieces.append(e)
                parent_counts[key] = len(e)
    parents = pd.concat(pieces, ignore_index=True)
    paths = extract_paths(parents, temp)
    paths.to_parquet(a.output_dir / "path_prices.parquet", index=False)
    rows = []
    for formation in FORMATIONS:
        for absolute in ABSOLUTE:
            for surprise in SURPRISE:
                key = f"f{formation}_a{int(absolute*100)}_s{int(surprise)}"
                base_paths = paths[paths["anchor"] == key]
                for volume_threshold in VOLUME:
                    filtered = base_paths[base_paths["volume_ratio"] >= volume_threshold]
                    parent_events = filtered[
                        ["symbol", "date", "event_ts"]
                    ].drop_duplicates().shape[0]
                    for mode in CONFIRMATIONS:
                        delay = MODES[mode]
                        for hold in HOLDS:
                            eligible = event_table(filtered, mode, hold)
                            metrics, selected = evaluate(eligible, delay, hold)
                            ex = selected[selected["date"] != pd.Timestamp("2025-04-07")]
                            row = {
                                "formation": formation, "residual_absolute": absolute,
                                "stock_surprise": surprise,
                                "volume_threshold": volume_threshold,
                                "confirmation": mode, "hold": hold,
                                "threshold_parent_events": parent_counts[key],
                                "volume_parent_events": parent_events,
                                "confirmed_events": len(eligible),
                                "confirmation_attrition": parent_events-len(eligible),
                                "portfolio_events": metrics["events"],
                                "portfolio_clusters": metrics["clusters"],
                                "net": metrics["net"],
                                "net_ex_20250407": float(ex["weighted_net"].sum()),
                                "max_drawdown": metrics["max_drawdown"],
                                "recovery_days": metrics["recovery_days"],
                                "unresolved": metrics["unresolved"],
                            }
                            for label, values in metrics["windows"].items():
                                for name, value in values.items():
                                    row[f"{label}_{name}"] = value
                            rows.append(row)
    if len(rows) != 486:
        raise RuntimeError(f"variant mismatch {len(rows)}")
    grid = pd.DataFrame(rows).sort_values("15m_avg_month", ascending=False)
    grid.to_csv(a.output_dir / "reclaim_grid.csv", index=False)
    diagnostics = {
        "parent_counts": parent_counts, "path_rows": len(paths),
        "positive_variants": int((grid["net"] > 0).sum()),
        "positive_ex_20250407_variants": int((grid["net_ex_20250407"] > 0).sum()),
        "leaders_by_15m_avg_month": grid.head(40).to_dict(orient="records"),
    }
    (a.output_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    contract = {
        "executed_variant_count": 486, "expected_variant_count": 486,
        "events_sha256": hashlib.sha256(a.events.read_bytes()).hexdigest(),
        "formations": FORMATIONS, "residual_absolute": ABSOLUTE,
        "stock_surprise": SURPRISE, "volume_thresholds": VOLUME,
        "confirmations": CONFIRMATIONS, "holds": HOLDS, "universe": "stocks",
        "loaded_max_date": str(paths["date"].max().date()),
        "holdout_rows_loaded": int((paths["date"] >= "2026-05-01").sum()),
    }
    if contract["holdout_rows_loaded"]:
        raise RuntimeError("holdout contamination")
    (a.output_dir / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(grid.head(50).to_string(index=False))


if __name__ == "__main__":
    main()
