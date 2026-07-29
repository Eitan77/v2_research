from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from run0001 import summarize_variant


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--events", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    e = pd.read_parquet(a.events)
    e["date"] = pd.to_datetime(e["date"])
    e["shock"] = -e["ret60"]
    e["surprise"] = e["shock"] / e["prior60_normal"]
    rows = []
    for absolute in [0.04, 0.05, 0.06, 0.08]:
        for relative in [8.0, 10.0]:
            subset = e[(e["shock"] >= absolute) & (e["surprise"] >= relative)]
            for hold in [30, 60]:
                metrics, _ = summarize_variant(subset, hold, 10.0)
                without = subset[subset["date"] != pd.Timestamp("2025-04-07")]
                ex_metrics, _ = summarize_variant(without, hold, 10.0)
                row = {
                    "absolute": absolute, "relative": relative, "hold": hold,
                    "candidate_events": len(subset), "net": metrics["net"],
                    "max_dd": metrics["max_drawdown"], "recovery": metrics["recovery_days"],
                    "positive_fraction": metrics["positive_event_fraction"],
                    "net_ex_20250407": ex_metrics["net"],
                }
                for label in ["18m", "15m", "12m"]:
                    row[f"{label}_avg_month"] = metrics["windows"][label]["avg_month"]
                    row[f"{label}_negative_months"] = metrics["windows"][label]["negative_months"]
                    row[f"{label}_events"] = metrics["windows"][label]["events"]
                rows.append(row)
    if len(rows) != 16:
        raise RuntimeError("variant count mismatch")
    out = pd.DataFrame(rows).sort_values("15m_avg_month", ascending=False)
    out.to_csv(a.output_dir / "neighborhood.csv", index=False)
    contract = {"executed_variant_count": 16, "expected_variant_count": 16,
                "loaded_max_date": str(e["date"].max().date()),
                "holdout_rows_loaded": int((e["date"] >= "2026-05-01").sum())}
    (a.output_dir / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(out.to_json(orient="records", indent=2))


if __name__ == "__main__":
    main()
