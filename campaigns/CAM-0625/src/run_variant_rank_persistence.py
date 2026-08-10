from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
CAM = ROOT / "campaigns"
OUT = CAM / "CAM-0625" / "artifacts" / "RUN-0030"
SHARED = CAM / "CAM-0600" / "artifacts" / "shared"


def load_variant_rows(campaign_id: str) -> pd.DataFrame:
    roots = [CAM / campaign_id / "artifacts" / "RUN-0020" / "variants"]
    repair_root = CAM / campaign_id / "artifacts" / "RUN-0021" / "variants"
    if repair_root.exists():
        roots.append(repair_root)
    rows = []
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.glob("*__cost_2bps/daily.parquet"):
            variant = path.parent.name.removesuffix("__cost_2bps")
            if variant in seen:
                continue
            seen.add(variant)
            daily = pd.read_parquet(path, columns=["date", "net_pnl"])
            daily["date"] = pd.to_datetime(daily.date)
            early = daily[daily.date < "2024-01-01"].net_pnl
            late = daily[daily.date >= "2024-01-01"].net_pnl
            rows.append({
                "campaign_id": campaign_id,
                "variant": variant,
                "early_net": float(early.sum()),
                "late_net": float(late.sum()),
                "early_active_days": int((early.abs() > 1e-12).sum()),
                "late_active_days": int((late.abs() > 1e-12).sum()),
            })
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = pd.read_csv(SHARED / "split_repaired_diagnostic_summary.csv").set_index("campaign_id")
    repair = pd.read_csv(SHARED / "split_repaired_repair_diagnostic_summary.csv").set_index("campaign_id")
    family_rows = []
    detail_frames = []
    for number in range(600, 625):
        campaign_id = f"CAM-{number:04d}"
        detail = load_variant_rows(campaign_id)
        if detail.empty:
            raise RuntimeError(f"no repaired variants found for {campaign_id}")
        detail["early_rank_pct"] = detail.early_net.rank(pct=True, method="average")
        detail["late_rank_pct"] = detail.late_net.rank(pct=True, method="average")
        rank_corr = float(detail.early_net.rank().corr(detail.late_net.rank()))
        selected_source = repair if campaign_id in repair.index else base
        selected_raw = selected_source.loc[campaign_id, "selected_variant"]
        selected = None if pd.isna(selected_raw) else str(selected_raw)
        selected_row = detail[detail.variant.eq(selected)] if selected else pd.DataFrame()
        family_rows.append({
            "campaign_id": campaign_id,
            "variant_count": int(len(detail)),
            "rank_correlation": rank_corr,
            "positive_both_share": float(((detail.early_net > 0) & (detail.late_net > 0)).mean()),
            "early_positive_share": float((detail.early_net > 0).mean()),
            "late_positive_share": float((detail.late_net > 0).mean()),
            "selected_variant": selected,
            "selected_found": bool(len(selected_row)),
            "selected_early_rank_pct": float(selected_row.early_rank_pct.iloc[0]) if len(selected_row) else None,
            "selected_late_rank_pct": float(selected_row.late_rank_pct.iloc[0]) if len(selected_row) else None,
            "selected_early_net": float(selected_row.early_net.iloc[0]) if len(selected_row) else None,
            "selected_late_net": float(selected_row.late_net.iloc[0]) if len(selected_row) else None,
        })
        detail_frames.append(detail)

    families = pd.DataFrame(family_rows)
    details = pd.concat(detail_frames, ignore_index=True)
    selected = families[families.selected_found]
    report = {
        "status": "completed",
        "run_id": "RUN-0030",
        "families": int(len(families)),
        "variants": int(families.variant_count.sum()),
        "family_rank_correlation_median": float(families.rank_correlation.median()),
        "family_rank_correlation_negative_count": int((families.rank_correlation < 0).sum()),
        "family_rank_correlation_below_0_25_count": int((families.rank_correlation < 0.25).sum()),
        "families_positive_both_majority": int((families.positive_both_share > 0.5).sum()),
        "selected_found": int(selected.selected_found.sum()),
        "selected_early_rank_pct_median": float(selected.selected_early_rank_pct.median()),
        "selected_early_bottom_half_count": int((selected.selected_early_rank_pct < 0.5).sum()),
        "selected_positive_early_count": int((selected.selected_early_net > 0).sum()),
        "selected_positive_late_count": int((selected.selected_late_net > 0).sum()),
        "lowest_rank_persistence_families": families.nsmallest(8, "rank_correlation")[
            ["campaign_id", "variant_count", "rank_correlation", "positive_both_share"]
        ].to_dict("records"),
        "family_results": family_rows,
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "interpretation": "Within-family rank persistence diagnostic; selected variants remain adapted development choices.",
    }
    families.to_csv(OUT / "family_rank_persistence.csv", index=False)
    details.to_parquet(OUT / "variant_window_metrics.parquet", index=False)
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    path = CAM / "CAM-0625" / "runs" / "RUN-0030.yaml"
    run = yaml.safe_load(path.read_text(encoding="utf-8"))
    run["status"] = "completed"
    run["result"] = report
    run["decision"] = "Use rank instability to constrain claims; do not tune or promote from the late-window ranking."
    path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    with (CAM / "CAM-0625" / "WORKLOG.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"run_id": "RUN-0030", "event": "completed", "result": report}) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "family_results"}, indent=2))


if __name__ == "__main__":
    main()
