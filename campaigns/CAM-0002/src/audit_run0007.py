from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from run0001 import summarize_variant
from run0007 import anchor_events


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--events", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    e = pd.read_parquet(a.events)
    e["date"] = pd.to_datetime(e["date"])
    e["raw_shock"] = -e["formation_return"]
    base = anchor_events(e, (15, "residual", 0.06, 6.0))
    subset = base[(base["asset_class"] == "stocks") & (base["volume_ratio"] >= 5)].copy()
    metrics, selected = summarize_variant(subset, 60, 10.0)
    selected.to_csv(a.output_dir / "leader_events.csv", index=False)
    symbols = selected.groupby("symbol").agg(
        events=("weighted_net", "size"),
        net_contribution=("weighted_net", "sum"),
        mean_event_net=("net_return", "mean"),
    ).sort_values("net_contribution", ascending=False)
    symbols.to_csv(a.output_dir / "leader_symbols.csv")
    positive = selected.loc[selected["weighted_net"] > 0, "weighted_net"].sum()
    ordered = selected.sort_values("weighted_net", ascending=False)
    diagnostics = {
        "configuration": "abrupt_residual15 volume>=5 stocks hold60 cost10",
        "metrics": metrics,
        "top1_positive_contribution_fraction": (
            float(ordered.iloc[0]["weighted_net"] / positive) if positive else None
        ),
        "top5_positive_contribution_fraction": (
            float(ordered.head(5)["weighted_net"].sum() / positive) if positive else None
        ),
        "unique_symbols": int(selected["symbol"].nunique()),
        "top_symbols": symbols.head(10).reset_index().to_dict(orient="records"),
    }
    (a.output_dir / "leader_diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")


if __name__ == "__main__":
    main()
