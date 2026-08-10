from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "campaigns" / "CAM-0600" / "src"
sys.path.insert(0, str(SRC))

import run_sma_rank_neighborhood as sweep
from deep_strategies import liquid_mask
from suite_core import evaluate_weights, write_json

OUT = sweep.OUT


def choose(training: pd.DataFrame) -> pd.Series:
    fidx = {v: i for i, v in enumerate(sweep.FORMATIONS)}
    sidx = {v: i for i, v in enumerate(sweep.SKIPS)}
    rows = []
    for row in training.itertuples(index=False):
        neighborhood = training.loc[
            training.formation.map(fidx).sub(fidx[row.formation]).abs().le(1)
            & training.skip.map(sidx).sub(sidx[row.skip]).abs().le(1)
        ]
        rec = row._asdict()
        rec["neighbor_train_min"] = float(neighborhood.train_net.min())
        rec["neighbor_train_median"] = float(neighborhood.train_net.median())
        rec["neighbor_cells"] = len(neighborhood)
        rows.append(rec)
    ranked = pd.DataFrame(rows).sort_values(
        ["neighbor_train_min", "train_drawdown", "formation", "skip"],
        ascending=[False, True, True, True], kind="stable"
    )
    return ranked.iloc[0]


def build_walkforward_weights(panel, condition, signals, top_k: int, decisions: pd.DataFrame, liquid: np.ndarray) -> np.ndarray:
    cache = {}
    combined = np.zeros_like(panel.adj_close, dtype=float)
    for row in decisions.itertuples(index=False):
        key = (int(row.formation), int(row.skip))
        if key not in cache:
            cache[key], _ = sweep.build_weights(panel, condition, signals, key[0], key[1], top_k, liquid)
        mask = panel.dates.year == int(row.evaluation_year)
        combined[mask] = cache[key][mask]
    return combined


def main() -> None:
    decision_rows = []
    metric_rows = []
    config_rows = pd.read_csv(OUT / "quote_candidate_configs.csv").to_dict("records")
    for family, panel, condition, signals in sweep.definitions():
        liquid = liquid_mask(panel, 0.50)
        common_start = pd.Timestamp(panel.dates[sweep.COMMON_START_INDEX])
        param_daily = {}
        param_weights = {}
        for formation in sweep.FORMATIONS:
            for skip in sweep.SKIPS:
                weights, _ = sweep.build_weights(panel, condition, signals, formation, skip, 1, liquid)
                # Ranking does not depend on breadth, but portfolio P&L does; weights are rebuilt below.
                param_weights[(formation, skip, 1)] = weights
        for top_k in sweep.BREADTHS:
            for formation in sweep.FORMATIONS:
                for skip in sweep.SKIPS:
                    if top_k == 1:
                        weights = param_weights[(formation, skip, 1)]
                    else:
                        weights, _ = sweep.build_weights(panel, condition, signals, formation, skip, top_k, liquid)
                    param_weights[(formation, skip, top_k)] = weights
                    _, daily, _, _, _ = evaluate_weights(panel, weights, 2.0, holding="open_to_next_open", execution_lag=1)
                    param_daily[(formation, skip, top_k)] = daily.net_pnl
            family_decisions = []
            for year in sorted(set(panel.dates.year)):
                evaluation_start = pd.Timestamp(f"{year}-01-01")
                prior = panel.dates[(panel.dates >= common_start) & (panel.dates < evaluation_start)]
                if len(prior) < 252:
                    continue
                training_rows = []
                for formation in sweep.FORMATIONS:
                    for skip in sweep.SKIPS:
                        pnl = param_daily[(formation, skip, top_k)]
                        train = pnl.loc[(pnl.index >= common_start) & (pnl.index < evaluation_start)]
                        training_rows.append({
                            "formation": formation,
                            "skip": skip,
                            "train_net": float(train.sum()),
                            "train_drawdown": sweep.dd(train),
                        })
                chosen = choose(pd.DataFrame(training_rows))
                rec = {
                    "family": family,
                    "panel": panel.name,
                    "top_k": top_k,
                    "evaluation_year": int(year),
                    "training_start": str(common_start.date()),
                    "training_end": str(pd.Timestamp(prior.max()).date()),
                    "training_sessions": len(prior),
                    "formation": int(chosen.formation),
                    "skip": int(chosen.skip),
                    "neighbor_train_min": float(chosen.neighbor_train_min),
                    "neighbor_train_median": float(chosen.neighbor_train_median),
                }
                decision_rows.append(rec)
                family_decisions.append(rec)
            decisions = pd.DataFrame(family_decisions)
            wf = build_walkforward_weights(panel, condition, signals, top_k, decisions, liquid)
            baseline = np.zeros_like(wf)
            baseline_source = param_weights[(126, 21, top_k)]
            for year in decisions.evaluation_year:
                mask = panel.dates.year == int(year)
                baseline[mask] = baseline_source[mask]
            for label, weights in (("walkforward", wf), ("baseline_matched", baseline)):
                m2, daily2, _, _, sym2 = evaluate_weights(panel, weights, 2.0, holding="open_to_next_open", execution_lag=1)
                m10, _, _, _, _ = evaluate_weights(panel, weights, 10.0, holding="open_to_next_open", execution_lag=1)
                active = daily2.gross_exposure > 1e-12
                pnl = daily2.net_pnl.loc[active.index[active]] if active.any() else daily2.net_pnl.iloc[0:0]
                recent = daily2.net_pnl.loc[daily2.index >= pd.Timestamp("2025-05-01")]
                positive = sym2.net_pnl.clip(lower=0)
                metric_rows.append({
                    "family": family,
                    "top_k": top_k,
                    "role": label,
                    "first_evaluation_year": int(decisions.evaluation_year.min()),
                    "evaluation_years": len(decisions),
                    "net_2bps": float(pnl.sum()),
                    "net_10bps": float(m10["net_simple_return"]),
                    "drawdown_2bps": sweep.dd(pnl),
                    "recent12_net_2bps": float(recent.sum()),
                    "recent12_drawdown_2bps": sweep.dd(recent),
                    "top5_symbol_positive_share_2bps": float(positive.head(5).sum() / positive.sum()) if positive.sum() > 0 else None,
                    "leave_top5_return_2bps": float(pnl.sum() - sym2.net_pnl.head(5).sum()),
                    "trade_sessions": int((daily2.turnover > 1e-12).sum()),
                })
            config_rows.append({"family": family, "top_k": top_k, "formation": -1, "skip": -1, "role": "walkforward"})
    decisions = pd.DataFrame(decision_rows)
    metrics = pd.DataFrame(metric_rows)
    configs = pd.DataFrame(config_rows).drop_duplicates(["family", "top_k", "formation", "skip", "role"])
    decisions.to_csv(OUT / "walkforward_selections.csv", index=False)
    metrics.to_csv(OUT / "walkforward_bar_metrics.csv", index=False)
    configs.to_csv(OUT / "quote_candidate_configs.csv", index=False)
    write_json(OUT / "walkforward_report.json", {
        "status": "completed",
        "paths": int((metrics.role == "walkforward").sum()),
        "decision_rows": len(decisions),
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
    })
    paired = metrics.pivot(index=["family", "top_k"], columns="role", values=["net_2bps", "drawdown_2bps", "recent12_net_2bps"])
    print(paired.to_string())


if __name__ == "__main__":
    main()
