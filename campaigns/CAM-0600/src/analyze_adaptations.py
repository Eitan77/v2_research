from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from suite_core import CAMPAIGNS, write_json


CAMPAIGN_IDS = tuple(f"CAM-{i:04d}" for i in range(600, 625))
OUT = CAMPAIGNS / "CAM-0600" / "artifacts" / "shared"


def execution_qualified(row: pd.Series) -> bool:
    metadata = json.loads(str(row["metadata_json"]))
    mode = str(metadata.get("mode", "")).lower()
    short_rule = str(metadata.get("short_rule", "")).lower()
    if mode in {"long", "long_cash", "long_safest"}:
        return True
    blocked_words = ("diagnostic", "no-stop", "without protective stop", "overnight signal")
    if any(word in short_rule for word in blocked_words):
        return False
    variant = str(row["variant_id"]).lower()
    if any(word in variant for word in ("long_short", "dollar_neutral", "unconstrained", "alpha_combo", "pairs_")):
        return False
    return True


def detail_path(campaign_id: str, variant_id: str) -> Path:
    safe = f"{variant_id}__cost_2bps".replace("/", "_").replace(":", "_")
    return CAMPAIGNS / campaign_id / "artifacts" / "RUN-0002" / "variants" / safe / "daily.parquet"


def split_metrics(path: Path) -> dict:
    daily = pd.read_parquet(path)
    daily["date"] = pd.to_datetime(daily["date"])
    daily["year"] = daily["date"].dt.year
    yearly = daily.groupby("year")["net_pnl"].sum()
    post = daily[daily["date"] >= pd.Timestamp("2024-01-01")]["net_pnl"]
    pre = daily[daily["date"] < pd.Timestamp("2024-01-01")]["net_pnl"]
    return {
        "positive_years": int((yearly > 0).sum()),
        "negative_years": int((yearly < 0).sum()),
        "years": int(len(yearly)),
        "worst_year": float(yearly.min()) if len(yearly) else None,
        "best_year": float(yearly.max()) if len(yearly) else None,
        "pre2024_return": float(pre.sum()),
        "post2024_return": float(post.sum()),
        "post2024_green_day_rate": float((post > 0).mean()) if len(post) else None,
    }


def main() -> None:
    baseline = pd.read_csv(OUT / "baseline_summary.csv").set_index("campaign_id")
    frames = []
    for campaign_id in CAMPAIGN_IDS:
        path = CAMPAIGNS / campaign_id / "artifacts" / "RUN-0002" / "variant_metrics.csv"
        frame = pd.read_csv(path)
        frame["execution_qualified"] = frame.apply(execution_qualified, axis=1)
        frames.append(frame)
    all_metrics = pd.concat(frames, ignore_index=True)
    all_metrics.to_parquet(OUT / "all_adaptation_variant_metrics.parquet", index=False)

    rows = []
    for campaign_id, frame in all_metrics.groupby("campaign_id", sort=True):
        central = frame[frame["cost_bps_per_side"] == 2.0].copy()
        raw = central.sort_values("net_simple_return", ascending=False).iloc[0]
        qualified_frame = central[central["execution_qualified"]]
        qualified = qualified_frame.sort_values("net_simple_return", ascending=False).iloc[0] if len(qualified_frame) else None
        selected = qualified if qualified is not None else raw
        same = frame[frame["variant_id"] == selected["variant_id"]].set_index("cost_bps_per_side")
        returns = same["net_simple_return"].to_dict()
        months = selected["positive_months"] + selected["negative_months"] + selected["inactive_months"]
        split = split_metrics(detail_path(campaign_id, str(selected["variant_id"])))
        positive_variant_fraction = float((central["net_simple_return"] > 0).mean())
        profitable_10bps_fraction = float((
            frame[frame["cost_bps_per_side"] == 10.0]["net_simple_return"] > 0
        ).mean())
        rows.append({
            "campaign_id": campaign_id,
            "baseline_best_2bps_return": float(baseline.loc[campaign_id, "net_return_2bps"]),
            "raw_best_variant": raw["variant_id"],
            "raw_best_2bps_return": raw["net_simple_return"],
            "selected_executable_variant": selected["variant_id"] if qualified is not None else None,
            "selected_2bps_return": selected["net_simple_return"] if qualified is not None else None,
            "selected_5bps_return": returns.get(5.0) if qualified is not None else None,
            "selected_10bps_return": returns.get(10.0) if qualified is not None else None,
            "maximum_drawdown": selected["maximum_drawdown"] if qualified is not None else None,
            "monthly_average": selected["monthly_average"] if qualified is not None else None,
            "monthly_median": selected["monthly_median"] if qualified is not None else None,
            "positive_month_rate": float(selected["positive_months"] / months) if qualified is not None and months else None,
            "recent12_average_month": selected["recent12_average_month"] if qualified is not None else None,
            "entries": int(selected["entries"]) if qualified is not None else None,
            "top5_symbol_positive_share": selected["top5_symbol_positive_share"] if qualified is not None else None,
            "top5_day_positive_share": selected["top5_day_positive_share"] if qualified is not None else None,
            "leave_best_symbol_out_return": selected["leave_best_symbol_out_return"] if qualified is not None else None,
            "positive_variant_fraction_at_2bps": positive_variant_fraction,
            "profitable_variant_fraction_at_10bps": profitable_10bps_fraction,
            "quote_gate": bool(qualified is not None and returns.get(2.0, -1) > 0),
            "basic_candidate_screen": bool(
                qualified is not None
                and returns.get(2.0, -1) > 0
                and selected["monthly_median"] > 0
                and selected["maximum_drawdown"] <= .30
                and selected["recent12_average_month"] > 0
                and selected["top5_day_positive_share"] <= .35
                and split["post2024_return"] > 0
                and split["negative_years"] <= 2
            ),
            **split,
        })
    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "adaptation_summary.csv", index=False)
    write_json(OUT / "adaptation_summary.json", {"campaigns": summary.to_dict(orient="records")})
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
