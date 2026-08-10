from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0600" / "artifacts" / "RUN-0044"


def main() -> None:
    grid = pd.read_csv(OUT / "bar_grid_metrics.csv")
    rows = []
    for (family, top_k), group in grid.groupby(["family", "top_k"], sort=True):
        baseline = group.loc[group.formation.eq(126) & group["skip"].eq(21)].iloc[0]
        dominant = group.loc[
            group.full_net_2bps.gt(baseline.full_net_2bps)
            & group.full_net_10bps.gt(baseline.full_net_10bps)
            & group.validation_net.gt(baseline.validation_net)
            & group.recent12_net.gt(baseline.recent12_net)
            & group.full_drawdown_2bps.le(baseline.full_drawdown_2bps + 1e-12)
        ].copy()
        for row in dominant.itertuples(index=False):
            rows.append({
                "family": family,
                "top_k": int(top_k),
                "formation": int(row.formation),
                "skip": int(row.skip),
                "role": "posthoc_bar_dominant",
                "full_improvement": float(row.full_net_2bps - baseline.full_net_2bps),
                "validation_improvement": float(row.validation_net - baseline.validation_net),
                "recent12_improvement": float(row.recent12_net - baseline.recent12_net),
                "drawdown_change": float(row.full_drawdown_2bps - baseline.full_drawdown_2bps),
            })
    dominant = pd.DataFrame(rows)
    dominant.to_csv(OUT / "posthoc_bar_dominant_configs.csv", index=False)
    configs = pd.read_csv(OUT / "quote_candidate_configs.csv")
    additions = dominant[["family", "top_k", "formation", "skip", "role"]]
    configs = pd.concat([configs, additions], ignore_index=True)
    configs = configs.sort_values(["family", "top_k", "formation", "skip", "role"])
    configs = configs.drop_duplicates(["family", "top_k", "formation", "skip"], keep="first")
    configs.to_csv(OUT / "quote_candidate_configs.csv", index=False)
    print(dominant.groupby(["family", "top_k"]).size().rename("dominant_cells").to_string())
    print(f"dominant={len(dominant)} total_quote_candidates={len(configs)}")


if __name__ == "__main__":
    main()
