from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from suite_core import CAMPAIGNS, write_json


CAMPAIGN_IDS = tuple(f"CAM-{i:04d}" for i in range(600, 625))
OUT = CAMPAIGNS / "CAM-0600" / "artifacts" / "shared"


def main() -> None:
    frames = []
    for campaign_id in CAMPAIGN_IDS:
        path = CAMPAIGNS / campaign_id / "artifacts" / "RUN-0001" / "variant_metrics.csv"
        frame = pd.read_csv(path)
        frame["campaign_id"] = campaign_id
        frames.append(frame)
    all_metrics = pd.concat(frames, ignore_index=True)
    all_metrics.to_parquet(OUT / "all_baseline_variant_metrics.parquet", index=False)

    rows = []
    for campaign_id, frame in all_metrics.groupby("campaign_id", sort=True):
        central = frame[frame["cost_bps_per_side"] == 2.0].sort_values(
            "net_simple_return", ascending=False
        )
        best = central.iloc[0]
        same = frame[frame["variant_id"] == best["variant_id"]].set_index("cost_bps_per_side")
        metadata = json.loads(str(best["metadata_json"]))
        short_rule = str(metadata.get("short_rule", ""))
        diagnostic = bool(metadata.get("signal_diagnostic", False)) or "diagnostic" in short_rule
        months = int(best["positive_months"] + best["negative_months"] + best["inactive_months"])
        positive_month_rate = float(best["positive_months"] / months) if months else 0.0
        recent_positive_rate = float(best["recent12_positive_months"] / max(1, best["recent12_months_observed"]))
        return_at = {float(k): float(v) for k, v in same["net_simple_return"].to_dict().items()}
        rows.append({
            "campaign_id": campaign_id,
            "best_variant_2bps": best["variant_id"],
            "panel": best["panel"],
            "holding": best["holding"],
            "net_return_m1bps": return_at.get(-1.0),
            "net_return_0bps": return_at.get(0.0),
            "net_return_1bps": return_at.get(1.0),
            "net_return_2bps": return_at.get(2.0),
            "net_return_5bps": return_at.get(5.0),
            "net_return_10bps": return_at.get(10.0),
            "maximum_drawdown": best["maximum_drawdown"],
            "monthly_average": best["monthly_average"],
            "monthly_median": best["monthly_median"],
            "positive_month_rate": positive_month_rate,
            "recent12_average_month": best["recent12_average_month"],
            "recent12_positive_rate": recent_positive_rate,
            "entries": int(best["entries"]),
            "active_days": int(best["active_days"]),
            "top5_symbol_positive_share": best["top5_symbol_positive_share"],
            "top5_day_positive_share": best["top5_day_positive_share"],
            "leave_best_symbol_out_return": best["leave_best_symbol_out_return"],
            "diagnostic_short_or_execution_blocked": diagnostic,
            "passes_low_cost_profit_gate": bool(return_at.get(2.0, -1) > 0),
            "passes_basic_consistency_screen": bool(
                return_at.get(2.0, -1) > 0
                and best["monthly_median"] > 0
                and positive_month_rate >= 0.55
                and best["maximum_drawdown"] <= 0.30
                and best["recent12_average_month"] > 0
                and best["top5_day_positive_share"] <= 0.35
            ),
        })
    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "baseline_summary.csv", index=False)
    write_json(OUT / "baseline_summary.json", {"campaigns": summary.to_dict(orient="records")})
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
