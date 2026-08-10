from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from suite_core import CAMPAIGNS

CAMPAIGN_IDS = tuple(f"CAM-{i:04d}" for i in range(600, 625))
SHARED = CAMPAIGNS / "CAM-0600" / "artifacts" / "shared"


def block_metrics(path: Path) -> dict:
    daily = pd.read_parquet(path)
    daily["date"] = pd.to_datetime(daily.date)
    active = daily[daily.gross_exposure > 1e-12]
    rows = []
    for number, idx in enumerate(np.array_split(np.arange(len(active)), 3), 1):
        if not len(idx):
            continue
        part = active.iloc[idx]
        months = max(1, part.date.dt.to_period("M").nunique())
        net = float(part.net_pnl.sum())
        rows.append({"block": number, "start": str(part.date.iloc[0].date()), "end": str(part.date.iloc[-1].date()), "net": net, "average_month": net / months})
    return {"positive_blocks": sum(row["net"] > 0 for row in rows), "worst_block_average_month": min((row["average_month"] for row in rows), default=np.nan), "block_returns": rows}


def main() -> None:
    summary_rows, selected_rows = [], []
    old_path = SHARED / "deep_diagnostic_summary.csv"
    old = pd.read_csv(old_path).set_index("campaign_id") if old_path.exists() else pd.DataFrame()
    for campaign_id in CAMPAIGN_IDS:
        root = CAMPAIGNS / campaign_id / "artifacts" / "RUN-0020"
        metrics = pd.read_csv(root / "variant_metrics.csv")
        central = metrics[metrics.cost_bps_per_side == 2.0].copy()
        ten = metrics[metrics.cost_bps_per_side == 10.0][["variant_id", "net_simple_return"]].rename(columns={"net_simple_return": "net_10bps"})
        central = central.merge(ten, on="variant_id", validate="one_to_one")
        blocks = []
        for row in central.itertuples():
            safe = f"{row.variant_id}__cost_2bps".replace("/", "_").replace(":", "_")
            blocks.append(block_metrics(root / "variants" / safe / "daily.parquet"))
        central["positive_blocks"] = [x["positive_blocks"] for x in blocks]
        central["worst_block_average_month"] = [x["worst_block_average_month"] for x in blocks]
        central["block_returns_json"] = [json.dumps(x["block_returns"], sort_keys=True) for x in blocks]
        central["structured_screen"] = ((central.net_simple_return > 0) & (central.net_10bps > 0) & (central.recent12_average_month > 0) & (central.recent12_positive_months >= 8) & (central.recent18_positive_months >= 11) & (central.maximum_drawdown <= .40) & (central.top5_day_positive_share.fillna(1.0) <= .15) & (central.entries >= 10) & (central.positive_blocks >= 2))
        eligible = central[central.structured_screen]
        identity_blocker = campaign_id == "CAM-0606"
        if len(eligible) and not identity_blocker:
            chosen = eligible.sort_values(["positive_blocks", "worst_block_average_month", "recent18_average_month", "net_simple_return"], ascending=False).iloc[0]
            selected = str(chosen.variant_id)
            decision = "provisional_repaired_target_change_quote_gate"
            selected_rows.append(chosen.to_dict())
        else:
            chosen = central.sort_values("net_simple_return", ascending=False).iloc[0]
            selected = None
            decision = "identity_blocked_requires_pair_run" if identity_blocker else "no_repaired_structured_survivor"
        previous = old.loc[campaign_id] if len(old) and campaign_id in old.index else None
        row = {"campaign_id": campaign_id, "executed_variants": len(central), "structured_survivors": len(eligible), "selected_variant": selected, "decision": decision, "raw_best_variant": str(central.sort_values("net_simple_return", ascending=False).iloc[0].variant_id), "raw_best_2bps": float(central.net_simple_return.max()), "selected_2bps": float(chosen.net_simple_return) if selected else None, "selected_recent12_average": float(chosen.recent12_average_month) if selected else None, "selected_recent12_positive": int(chosen.recent12_positive_months) if selected else None, "selected_recent18_average": float(chosen.recent18_average_month) if selected else None, "selected_recent18_positive": int(chosen.recent18_positive_months) if selected else None, "selected_maximum_drawdown": float(chosen.maximum_drawdown) if selected else None, "selected_positive_blocks": int(chosen.positive_blocks) if selected else None, "selected_worst_block_average_month": float(chosen.worst_block_average_month) if selected else None, "selected_entries": int(chosen.entries) if selected else None, "selected_top5_day_share": float(chosen.top5_day_positive_share) if selected else None, "prior_selected_variant": str(previous.selected_variant) if previous is not None and pd.notna(previous.selected_variant) else None, "prior_selected_2bps": float(previous.selected_2bps) if previous is not None and pd.notna(previous.selected_2bps) else None, "selection_changed": bool(previous is None or str(previous.selected_variant) != str(selected)), "holdout_rows_loaded": 0}
        summary_rows.append(row)
        run_path = CAMPAIGNS / campaign_id / "runs" / "RUN-0020.yaml"
        run = yaml.safe_load(run_path.read_text(encoding="utf-8"))
        run["result"]["structured_analysis"] = row
        run["decision"] = decision
        run_path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
        with (CAMPAIGNS / campaign_id / "WORKLOG.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"run_id": "RUN-0020", "event": "structured_analysis_completed", "decision": decision, "selected_variant": selected, "holdout_rows_loaded": 0}) + "\n")
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(SHARED / "split_repaired_diagnostic_summary.csv", index=False)
    pd.DataFrame(selected_rows).to_csv(SHARED / "split_repaired_selected_candidates.csv", index=False)
    print(summary[["campaign_id", "structured_survivors", "selected_variant", "selected_2bps", "selected_maximum_drawdown", "selection_changed"]].to_string(index=False))


if __name__ == "__main__":
    main()
