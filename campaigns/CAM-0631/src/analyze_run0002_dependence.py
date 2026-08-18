from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0631" / "artifacts" / "RUN-0002"


def main() -> None:
    data = pd.read_parquet(OUT / "validation_events_100ms.parquet")
    markouts = [column for column in data if column.startswith("markout_")]
    equal_symbol = (
        data.groupby(["symbol", "session", "bucket"], as_index=False)[markouts]
        .mean()
        .groupby(["session", "bucket"], as_index=False)[markouts]
        .mean()
    )
    equal_symbol.to_csv(OUT / "validation_equal_symbol_cluster_markouts.csv", index=False)
    report_path = OUT / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = equal_symbol[equal_symbol.bucket.isin(["low", "high"])].copy()
    report["dependency_diagnostic"] = {
        "unit": "100ms clusters then equal weight by symbol",
        "rows": json.loads(rows.to_json(orient="records")),
        "interpretation": "low bucket exceeds high bucket at 5s on both validation sessions after dependence and symbol weighting, but long-horizon raw-event means are not stable on 2025-11-05",
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(equal_symbol.to_string(index=False))


if __name__ == "__main__":
    main()
