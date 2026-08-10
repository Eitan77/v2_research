from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from suite_core import CAMPAIGNS


CAMPAIGN_IDS = tuple(f"CAM-{i:04d}" for i in range(600, 625))
SHARED = CAMPAIGNS / "CAM-0600" / "artifacts" / "shared"


def daily_path(campaign_id: str, variant_id: str) -> Path:
    safe = f"{variant_id}__cost_2bps".replace("/", "_").replace(":", "_")
    return CAMPAIGNS / campaign_id / "artifacts" / "RUN-0008" / "variants" / safe / "daily.parquet"


def block_metrics(path: Path) -> dict:
    d = pd.read_parquet(path)
    d["date"] = pd.to_datetime(d["date"])
    active = d[d["gross_exposure"] > 1e-12].copy()
    if active.empty:
        return {"positive_blocks": 0, "worst_block_average_month": np.nan, "block_returns": []}
    dates = np.array_split(np.arange(len(active)), 3)
    rows = []
    for k, idx in enumerate(dates, 1):
        x = active.iloc[idx]
        months = max(1, x["date"].dt.to_period("M").nunique())
        net = float(x["net_pnl"].sum())
        rows.append({"block": k, "start": str(x["date"].iloc[0].date()), "end": str(x["date"].iloc[-1].date()), "net": net, "average_month": net/months})
    return {
        "positive_blocks": int(sum(x["net"] > 0 for x in rows)),
        "worst_block_average_month": float(min(x["average_month"] for x in rows)),
        "block_returns": rows,
    }


def main() -> None:
    summary_rows = []
    selected_rows = []
    for campaign_id in CAMPAIGN_IDS:
        metrics_path = CAMPAIGNS / campaign_id / "artifacts" / "RUN-0008" / "variant_metrics.csv"
        metrics = pd.read_csv(metrics_path)
        central = metrics[metrics["cost_bps_per_side"] == 2.0].copy()
        ten = metrics[metrics["cost_bps_per_side"] == 10.0][["variant_id", "net_simple_return"]].rename(columns={"net_simple_return":"net_10bps"})
        central = central.merge(ten, on="variant_id", how="left", validate="one_to_one")
        blocks = []
        for row in central.itertuples(index=False):
            b = block_metrics(daily_path(campaign_id, str(row.variant_id)))
            blocks.append(b)
        central["positive_blocks"] = [x["positive_blocks"] for x in blocks]
        central["worst_block_average_month"] = [x["worst_block_average_month"] for x in blocks]
        central["block_returns_json"] = [json.dumps(x["block_returns"], sort_keys=True) for x in blocks]
        central["structured_screen"] = (
            (central["net_simple_return"] > 0)
            & (central["net_10bps"] > 0)
            & (central["recent12_average_month"] > 0)
            & (central["recent12_positive_months"] >= 8)
            & (central["recent18_positive_months"] >= 11)
            & (central["maximum_drawdown"] <= .40)
            & (central["top5_day_positive_share"].fillna(1.0) <= .15)
            & (central["entries"] >= 10)
            & (central["positive_blocks"] >= 2)
        )
        eligible = central[central["structured_screen"]].copy()
        identity_blocker = campaign_id == "CAM-0606"
        if len(eligible) and not identity_blocker:
            chosen = eligible.sort_values(
                ["positive_blocks", "worst_block_average_month", "recent18_average_month", "net_simple_return"],
                ascending=False,
            ).iloc[0]
            selected = str(chosen["variant_id"])
            decision = "provisional_target_change_quote_gate"
            selected_rows.append(chosen.to_dict())
        else:
            chosen = central.sort_values("net_simple_return", ascending=False).iloc[0]
            selected = None
            decision = "identity_blocked_requires_pair_run" if identity_blocker else "no_structured_survivor"
        sp = central[central["panel"] == "sp500"].sort_values("net_simple_return", ascending=False)
        best_sp = sp.iloc[0] if len(sp) else None
        summary_rows.append({
            "campaign_id": campaign_id,
            "executed_variants": int(len(central)),
            "positive_fraction_2bps": float((central["net_simple_return"] > 0).mean()),
            "positive_fraction_10bps": float((central["net_10bps"] > 0).mean()),
            "structured_survivors": int(len(eligible)),
            "selected_variant": selected,
            "decision": decision,
            "raw_best_variant": str(chosen["variant_id"]) if selected is None else str(central.sort_values("net_simple_return",ascending=False).iloc[0]["variant_id"]),
            "raw_best_2bps": float(central["net_simple_return"].max()),
            "selected_2bps": float(chosen["net_simple_return"]) if selected else None,
            "selected_recent12_average": float(chosen["recent12_average_month"]) if selected else None,
            "selected_recent12_positive": int(chosen["recent12_positive_months"]) if selected else None,
            "selected_recent18_average": float(chosen["recent18_average_month"]) if selected else None,
            "selected_recent18_positive": int(chosen["recent18_positive_months"]) if selected else None,
            "selected_maximum_drawdown": float(chosen["maximum_drawdown"]) if selected else None,
            "selected_positive_blocks": int(chosen["positive_blocks"]) if selected else None,
            "selected_worst_block_average_month": float(chosen["worst_block_average_month"]) if selected else None,
            "selected_entries": int(chosen["entries"]) if selected else None,
            "selected_top5_day_share": float(chosen["top5_day_positive_share"]) if selected else None,
            "sp500_best_variant": str(best_sp["variant_id"]) if best_sp is not None else None,
            "sp500_best_2bps": float(best_sp["net_simple_return"]) if best_sp is not None else None,
            "holdout_rows_loaded": 0,
        })
        run_path = CAMPAIGNS / campaign_id / "runs" / "RUN-0008.yaml"
        run = yaml.safe_load(run_path.read_text(encoding="utf-8"))
        run["status"] = "completed"
        run["result"] = summary_rows[-1]
        run["decision"] = decision
        run_path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
        worklog = CAMPAIGNS / campaign_id / "WORKLOG.jsonl"
        with worklog.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_id":"RUN-0008","status":"completed","decision":decision,"selected_variant":selected,"holdout_rows_loaded":0}, sort_keys=True)+"\n")
    summary = pd.DataFrame(summary_rows)
    SHARED.mkdir(parents=True, exist_ok=True)
    summary.to_csv(SHARED / "deep_diagnostic_summary.csv", index=False)
    pd.DataFrame(selected_rows).to_csv(SHARED / "deep_selected_candidates.csv", index=False)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
