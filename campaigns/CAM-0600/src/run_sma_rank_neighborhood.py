from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "campaigns" / "CAM-0600" / "src"
sys.path.insert(0, str(SRC))

from baseline_strategies import eligible, moving_average
from deep_strategies import liquid_mask
from suite_core import (
    CAMPAIGNS,
    evaluate_weights,
    load_panels,
    month_end_indices,
    rank_weights,
    trailing_return,
    weekly_indices,
    write_json,
)

OUT = CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0044"
FORMATIONS = (42, 63, 84, 126, 189, 252)
SKIPS = (0, 5, 10, 21, 42)
BREADTHS = (1, 2, 3, 10)
COMMON_START_INDEX = max(FORMATIONS) + max(SKIPS)
CUTOFF = pd.Timestamp("2026-04-30")


def dd(pnl: pd.Series) -> float:
    if pnl.empty:
        return 0.0
    equity = 1.0 + pnl.cumsum()
    return float(((equity.cummax() - equity) / equity.cummax()).max())


def period_metrics(pnl: pd.Series, prefix: str) -> dict[str, float | int]:
    months = pnl.groupby(pnl.index.to_period("M")).sum()
    return {
        f"{prefix}_net": float(pnl.sum()),
        f"{prefix}_drawdown": dd(pnl),
        f"{prefix}_positive_months": int((months > 1e-12).sum()),
        f"{prefix}_negative_months": int((months < -1e-12).sum()),
        f"{prefix}_worst_month": float(months.min()) if len(months) else 0.0,
    }


def definitions():
    panels = load_panels()
    qqq, sp = panels["qqq"], panels["sp500"]
    if max(qqq.dates.max(), sp.dates.max()) > CUTOFF:
        raise RuntimeError("loaded panel crossed discovery cutoff")
    return [
        ("qqq_single_ma150_weekly", qqq, qqq.adj_close > moving_average(qqq, 150), weekly_indices(qqq.dates)),
        ("qqq_dual_ma50_200_weekly", qqq, moving_average(qqq, 50) > moving_average(qqq, 200), weekly_indices(qqq.dates)),
        ("qqq_triple_ma10_50_200_monthly", qqq, (moving_average(qqq, 10) > moving_average(qqq, 50)) & (moving_average(qqq, 50) > moving_average(qqq, 200)), month_end_indices(qqq.dates)),
        ("sp500_dual_ma50_200_weekly", sp, moving_average(sp, 50) > moving_average(sp, 200), weekly_indices(sp.dates)),
        ("sp500_triple_ma10_50_200_monthly", sp, (moving_average(sp, 10) > moving_average(sp, 50)) & (moving_average(sp, 50) > moving_average(sp, 200)), month_end_indices(sp.dates)),
    ]


def build_weights(panel, condition, signals, formation: int, skip: int, top_k: int, liquid: np.ndarray):
    score = trailing_return(panel, formation, skip)
    mask = eligible(panel) & condition & liquid & np.isfinite(score)
    weights = rank_weights(score, mask, signals, mode="long", top_k=top_k)
    if np.nanmin(weights) < -1e-12 or np.nanmax(np.abs(weights).sum(axis=1)) > 1.0 + 1e-12:
        raise RuntimeError("long-only or gross-exposure constraint failed")
    return weights, mask


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    universe_rows: list[dict] = []
    defs = definitions()
    for family, panel, condition, signals in defs:
        liquid = liquid_mask(panel, 0.50)
        if panel.n_dates <= COMMON_START_INDEX:
            raise RuntimeError(f"insufficient common history for {family}")
        common_dates = panel.dates[COMMON_START_INDEX:]
        split_pos = int(np.floor(len(common_dates) * 0.60))
        split_date = pd.Timestamp(common_dates[split_pos])
        common_start = pd.Timestamp(common_dates[0])
        recent_start = pd.Timestamp("2025-05-01")
        universe_rows.append({
            "family": family,
            "panel": panel.name,
            "panel_minimum_date": str(panel.dates.min().date()),
            "panel_maximum_date": str(panel.dates.max().date()),
            "panel_sessions": panel.n_dates,
            "panel_symbols": panel.n_symbols,
            "common_start_date": str(common_start.date()),
            "common_sessions": len(common_dates),
            "selection_end_date": str(pd.Timestamp(common_dates[split_pos - 1]).date()),
            "validation_start_date": str(split_date.date()),
            "validation_sessions": int((panel.dates >= split_date).sum()),
            "rows_removed_for_common_comparison": COMMON_START_INDEX,
            "holdout_rows_loaded": int((panel.dates >= pd.Timestamp("2026-05-01")).sum()),
        })
        for formation in FORMATIONS:
            for skip in SKIPS:
                for top_k in BREADTHS:
                    weights, mask = build_weights(panel, condition, signals, formation, skip, top_k, liquid)
                    metrics2, daily2, _, _, symbol2 = evaluate_weights(panel, weights, 2.0, holding="open_to_next_open", execution_lag=1)
                    metrics10, _, _, _, _ = evaluate_weights(panel, weights, 10.0, holding="open_to_next_open", execution_lag=1)
                    pnl = daily2.net_pnl
                    common = pnl.loc[pnl.index >= common_start]
                    train = common.loc[common.index < split_date]
                    validation = common.loc[common.index >= split_date]
                    recent = pnl.loc[pnl.index >= recent_start]
                    positive = symbol2.net_pnl.clip(lower=0)
                    top5_share = float(positive.head(5).sum() / positive.sum()) if positive.sum() > 0 else None
                    row = {
                        "family": family,
                        "panel": panel.name,
                        "formation": formation,
                        "skip": skip,
                        "top_k": top_k,
                        "variant": f"{family}_top{top_k}_f{formation}_s{skip}",
                        "eligible_cells": int(mask.sum()),
                        "active_sessions": int((daily2.gross_exposure > 1e-12).sum()),
                        "turnover": float(daily2.turnover.sum()),
                        "full_net_2bps": float(metrics2["net_simple_return"]),
                        "full_net_10bps": float(metrics10["net_simple_return"]),
                        "full_drawdown_2bps": float(metrics2["maximum_drawdown"]),
                        "top5_symbol_positive_share_2bps": top5_share,
                        "leave_top5_return_2bps": float(metrics2["net_simple_return"] - symbol2.net_pnl.head(5).sum()),
                    }
                    row.update(period_metrics(common, "common"))
                    row.update(period_metrics(train, "train"))
                    row.update(period_metrics(validation, "validation"))
                    row.update(period_metrics(recent, "recent12"))
                    rows.append(row)
    grid = pd.DataFrame(rows)
    if len(grid) != 600 or grid.variant.nunique() != 600:
        raise RuntimeError(f"expected 600 variants, got {len(grid)} rows/{grid.variant.nunique()} unique")
    if grid.full_net_2bps.isna().any():
        raise RuntimeError("missing grid metric")

    fidx = {v: i for i, v in enumerate(FORMATIONS)}
    sidx = {v: i for i, v in enumerate(SKIPS)}
    neighbor_rows = []
    selected_rows = []
    for (family, top_k), group in grid.groupby(["family", "top_k"], sort=True):
        local = group.copy()
        enriched = []
        for row in local.itertuples(index=False):
            neighbors = local.loc[
                local.formation.map(fidx).sub(fidx[row.formation]).abs().le(1)
                & local.skip.map(sidx).sub(sidx[row.skip]).abs().le(1)
            ]
            if len(neighbors) < 4:
                raise RuntimeError("parameter neighborhood unexpectedly sparse")
            rec = row._asdict()
            rec.update({
                "neighbor_cells": len(neighbors),
                "neighbor_train_min": float(neighbors.train_net.min()),
                "neighbor_train_median": float(neighbors.train_net.median()),
                "neighbor_validation_positive_fraction": float((neighbors.validation_net > 0).mean()),
                "neighbor_full_10bps_positive_fraction": float((neighbors.full_net_10bps > 0).mean()),
            })
            enriched.append(rec)
        e = pd.DataFrame(enriched)
        e = e.sort_values(
            ["neighbor_train_min", "train_drawdown", "formation", "skip"],
            ascending=[False, True, True, True],
            kind="stable",
        )
        chosen = e.iloc[0].copy()
        baseline = e.loc[e.formation.eq(126) & e.skip.eq(21)].iloc[0]
        chosen["selection_role"] = "training_plateau_center"
        chosen["baseline_variant"] = baseline.variant
        chosen["baseline_train_net"] = baseline.train_net
        chosen["baseline_validation_net"] = baseline.validation_net
        chosen["baseline_full_net_2bps"] = baseline.full_net_2bps
        chosen["baseline_recent12_net"] = baseline.recent12_net
        chosen["validation_improvement_vs_baseline"] = chosen.validation_net - baseline.validation_net
        chosen["full_improvement_vs_baseline"] = chosen.full_net_2bps - baseline.full_net_2bps
        selected_rows.append(chosen.to_dict())
        neighbor_rows.extend(enriched)

    selected = pd.DataFrame(selected_rows).sort_values(["family", "top_k"])
    neighbors = pd.DataFrame(neighbor_rows)
    summaries = grid.groupby(["family", "top_k"], as_index=False).agg(
        cells=("variant", "count"),
        full_2bps_positive_fraction=("full_net_2bps", lambda x: float((x > 0).mean())),
        full_10bps_positive_fraction=("full_net_10bps", lambda x: float((x > 0).mean())),
        validation_positive_fraction=("validation_net", lambda x: float((x > 0).mean())),
        median_full_net_2bps=("full_net_2bps", "median"),
        minimum_full_net_2bps=("full_net_2bps", "min"),
        maximum_full_net_2bps=("full_net_2bps", "max"),
    )
    quote_configs = []
    for row in selected.itertuples(index=False):
        quote_configs.append({"family": row.family, "top_k": int(row.top_k), "formation": int(row.formation), "skip": int(row.skip), "role": "selected"})
        quote_configs.append({"family": row.family, "top_k": int(row.top_k), "formation": 126, "skip": 21, "role": "baseline"})
    quote_configs = pd.DataFrame(quote_configs).drop_duplicates(["family", "top_k", "formation", "skip"])

    grid.to_csv(OUT / "bar_grid_metrics.csv", index=False)
    neighbors.to_csv(OUT / "bar_neighbor_metrics.csv", index=False)
    selected.to_csv(OUT / "training_selected_configs.csv", index=False)
    summaries.to_csv(OUT / "surface_summary.csv", index=False)
    quote_configs.to_csv(OUT / "quote_candidate_configs.csv", index=False)
    pd.DataFrame(universe_rows).to_csv(OUT / "sample_attrition.csv", index=False)
    write_json(OUT / "bar_execution_report.json", {
        "status": "completed",
        "run_id": "RUN-0044",
        "variant_count": len(grid),
        "selected_count": len(selected),
        "quote_candidate_count": len(quote_configs),
        "maximum_loaded_date": str(max(x[1].dates.max() for x in defs).date()),
        "holdout_rows_loaded": int(sum((x[1].dates >= pd.Timestamp("2026-05-01")).sum() for x in defs)),
        "fixed_capital_base": 1.0,
        "additive_noncompounded": True,
        "broker_margin": False,
    })
    print(selected[["family", "top_k", "formation", "skip", "full_net_2bps", "validation_net", "validation_improvement_vs_baseline", "recent12_net"]].to_string(index=False))
    print(f"grid={len(grid)} quote_candidates={len(quote_configs)}")


if __name__ == "__main__":
    main()
