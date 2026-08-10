from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0600" / "artifacts" / "RUN-0044"


def main() -> None:
    grid = pd.read_csv(OUT / "bar_grid_metrics.csv")
    selected = pd.read_csv(OUT / "training_selected_configs.csv")
    configs = pd.read_csv(OUT / "quote_candidate_configs.csv")
    quotes = pd.read_csv(OUT / "quote_metrics.csv")
    fills = pd.read_parquet(OUT / "fill_ledger.parquet")
    analysis = json.loads((OUT / "analysis_report.json").read_text(encoding="utf-8"))
    execution = json.loads((OUT / "execution_report.json").read_text(encoding="utf-8"))
    attrition = pd.read_csv(OUT / "sample_attrition.csv")
    run = yaml.safe_load((ROOT / "campaigns" / "CAM-0600" / "runs" / "RUN-0044.yaml").read_text(encoding="utf-8"))

    assert len(grid) == 600 and grid.variant.nunique() == 600
    assert len(selected) == 20
    assert len(configs) == 95
    assert quotes.candidate.nunique() == 95 and len(quotes) == 475
    assert quotes.role_coverage.min() == 1.0
    assert (quotes.loc[quotes.extra_adverse_bps_per_side.eq(10), "net_simple_return"] > 0).all()
    assert len(fills) == 41372 and fills.complete_both.all()
    assert analysis["posthoc_quote_robust_cells"] == 28
    assert analysis["training_selected_full_improvements"] == 5
    assert analysis["walkforward_matched_improvements"] == 2
    assert execution["status"] == "completed" and execution["candidate_count"] == 95
    assert execution["holdout_rows_loaded"] == 0
    assert attrition.holdout_rows_loaded.sum() == 0
    assert attrition.panel_maximum_date.max() == "2026-04-30"
    assert run["status"] == "completed" and run["result"]["candidate_count"] == 95
    for path in (
        ROOT / "campaigns" / "CAM-0600" / "RESULTS.yaml",
        ROOT / "campaigns" / "CAM-0610" / "RESULTS.yaml",
        ROOT / "campaigns" / "CAM-0611" / "RESULTS.yaml",
        ROOT / "campaigns" / "CAM-0612" / "RESULTS.yaml",
    ):
        yaml.safe_load(path.read_text(encoding="utf-8"))
    print("validated RUN-0044: 600 bar variants, 95 quote configs, 100% coverage, zero holdout rows")


if __name__ == "__main__":
    main()
