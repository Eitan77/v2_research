from __future__ import annotations

import json
from pathlib import Path

from suite_core import CAMPAIGNS, CATALOG, CUTOFF, load_panels, semantic_fixtures, sha256, write_json


def main() -> None:
    output = CAMPAIGNS / "CAM-0600" / "artifacts" / "shared"
    panels = load_panels()
    report = {
        "status": "passed",
        "catalog": str(CATALOG),
        "catalog_sha256": sha256(CATALOG),
        "discovery_cutoff": str(CUTOFF.date()),
        "semantic_fixtures": semantic_fixtures(),
        "hardware": {
            "duckdb_threads": 16,
            "host_logical_processors": 16,
            "host_physical_cores": 8,
            "ram_bytes": 34218086400,
            "gpu": "NVIDIA GeForce RTX 3080 Ti",
            "gpu_used_for_readiness": False,
            "reason_gpu_not_used": "Daily readiness matrices are small and loader-bound; GPU transfer would not accelerate the exact logic.",
        },
        "panels": {name: panel.readiness for name, panel in panels.items()},
        "holdout_rows_loaded_total": int(
            sum(
                int(panel.readiness.get("holdout_rows_loaded_total", panel.readiness.get("holdout_rows_loaded", 0)))
                for panel in panels.values()
            )
        ),
        "maximum_loaded_date": max(str(panel.dates.max().date()) for panel in panels.values()),
        "source_code": {
            "suite_core": sha256(Path(__file__).with_name("suite_core.py")),
            "qqq_loader": sha256(CAMPAIGNS / "CAM-0513" / "src" / "run_0001_quality_sma.py"),
            "sp500_loader": sha256(CAMPAIGNS / "CAM-0515" / "src" / "run_0007_sp500_top5.py"),
            "sp500_membership": sha256(CAMPAIGNS / "CAM-0508" / "artifacts" / "membership" / "sp500_pit_membership_daily.parquet"),
        },
    }
    if report["holdout_rows_loaded_total"] != 0 or report["maximum_loaded_date"] > "2026-04-30":
        raise RuntimeError("readiness holdout gate failed")
    write_json(output / "readiness.json", report)
    print(json.dumps({
        "status": report["status"],
        "maximum_loaded_date": report["maximum_loaded_date"],
        "holdout_rows_loaded_total": report["holdout_rows_loaded_total"],
        "panels": {
            name: {
                "dates": len(panel.dates),
                "symbols": len(panel.symbols),
                "min": str(panel.dates.min().date()),
                "max": str(panel.dates.max().date()),
            }
            for name, panel in panels.items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
