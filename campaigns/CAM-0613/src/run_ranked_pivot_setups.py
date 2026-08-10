from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
CAM = ROOT / "campaigns"
OUT = CAM / "CAM-0613" / "artifacts" / "RUN-0022"
sys.path.insert(0, str(CAM / "CAM-0600" / "src"))
from baseline_strategies import pivot_weights
from deep_strategies import liquid_mask, trend_mask
from suite_core import evaluate_weights, load_panels

FOLDS = [("2021", "2021-01-01", "2021-12-31"), ("2022_2023", "2022-01-01", "2023-12-31"), ("2024_2026apr", "2024-01-01", "2026-04-30")]


def select_weights(score, valid, top_k):
    out = np.zeros_like(score)
    for i in range(len(out)):
        cols = np.flatnonzero(valid[i] & np.isfinite(score[i]))
        if not len(cols):
            continue
        chosen = cols[np.argsort(score[i, cols], kind="stable")[-min(top_k, len(cols)):]]
        out[i, chosen] = 1.0 / len(chosen)
    return out


def fold_metrics(daily):
    values = {}
    for name, start, end in FOLDS:
        x = daily[(daily.index >= start) & (daily.index <= end)].net_pnl
        values[name] = float(x.sum())
    return values


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    panels = load_panels()
    rows = []
    daily_cache = {}
    for universe in ("sp500", "qqq"):
        panel = panels[universe]
        _, realized, source = pivot_weights(panel, "long")
        prev_high = np.vstack([np.full(panel.n_symbols, np.nan), panel.raw_high[:-1]])
        prev_low = np.vstack([np.full(panel.n_symbols, np.nan), panel.raw_low[:-1]])
        prev_close = np.vstack([np.full(panel.n_symbols, np.nan), panel.raw_close[:-1]])
        pivot = (prev_high + prev_low + prev_close) / 3
        resistance = 2 * pivot - prev_low
        prior_range = prev_high - prev_low
        setup = panel.member & np.isfinite(panel.raw_open) & np.isfinite(pivot) & np.isfinite(resistance) & (panel.raw_open > pivot) & (resistance > panel.raw_open)
        scores = {
            "target_room_pct": (resistance / panel.raw_open - 1),
            "closest_above_pivot_by_prior_range": -((panel.raw_open - pivot) / prior_range),
            "target_room_over_prior_range": (resistance - panel.raw_open) / prior_range,
        }
        volume_median = pd.DataFrame(panel.volume).rolling(20, min_periods=20).median().shift(1).to_numpy()
        for rank_rule, top_k, trend_window, volume_confirmation in itertools.product(scores, (5, 10, 20), (100, 200), (False, True)):
            valid = setup & liquid_mask(panel, 0.35) & trend_mask(panel, trend_window)
            if volume_confirmation:
                valid &= panel.volume > volume_median
            weights = select_weights(scores[rank_rule], valid, top_k)
            variant_id = f"{universe}__pivot_{rank_rule}__top{top_k}__sma{trend_window}__vol{int(volume_confirmation)}"
            record = {"variant_id": variant_id, "universe": universe, "ranking_rule": rank_rule, "top_k": top_k, "trend_window": trend_window, "volume_confirmation": volume_confirmation, "source_long_entries": source["long_entries"]}
            for cost in (2, 5, 10):
                metrics, daily, _, _, _ = evaluate_weights(panel, weights, cost, holding="return_override", execution_lag=0, return_override=realized)
                folds = fold_metrics(daily)
                for key in ("net_simple_return", "maximum_drawdown", "entries", "positive_months", "negative_months", "recent12_positive_months", "recent12_average_month", "top5_day_positive_share", "top5_symbol_positive_share"):
                    record[f"cost{cost}_{key}"] = metrics[key]
                for fold, value in folds.items():
                    record[f"cost{cost}_{fold}"] = value
                record[f"cost{cost}_worst_fold"] = min(folds.values())
                if cost == 2:
                    daily_cache[variant_id] = daily.reset_index()
            rows.append(record)
    frame = pd.DataFrame(rows)
    gate = frame.cost2_net_simple_return.gt(0) & frame.cost5_net_simple_return.gt(0) & frame.cost2_maximum_drawdown.le(0.20) & frame.cost2_worst_fold.gt(0) & frame.cost2_recent12_positive_months.ge(7) & frame.cost2_entries.ge(500) & frame.cost2_top5_day_positive_share.le(0.15)
    survivors = frame[gate].sort_values(["cost5_worst_fold", "cost2_recent12_average_month", "cost2_net_simple_return"], ascending=False)
    selected = survivors.iloc[0] if len(survivors) else None
    best = frame.nlargest(1, "cost2_net_simple_return").iloc[0]
    report = {"status": "completed" if selected is not None else "completed_no_candidate", "run_id": "RUN-0022", "variants": int(len(frame)), "structured_survivors": int(gate.sum()), "selected_variant": None if selected is None else str(selected.variant_id), "selected_metrics": None if selected is None else selected.to_dict(), "best_raw_2bps": best.to_dict(), "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0, "broker_margin": False, "direct_short": False, "interpretation": "Causal economic ranking replaces arbitrary equal-weight tie ordering; daily target hit remains a bar-stage execution model."}
    frame.to_csv(OUT / "variant_metrics.csv", index=False)
    if selected is not None:
        daily_cache[str(selected.variant_id)].to_parquet(OUT / "selected_daily.parquet", index=False)
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    path = CAM / "CAM-0613" / "runs" / "RUN-0022.yaml"; run = yaml.safe_load(path.read_text(encoding="utf-8")); run["status"] = report["status"]; run["result"] = json.loads(json.dumps(report, default=str)); run["decision"] = "Advance only a structured survivor to intraday target-order and quote replay; otherwise retain retirement after removing the arbitrary tie-selection artifact."; path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    with (CAM / "CAM-0613" / "WORKLOG.jsonl").open("a", encoding="utf-8") as handle: handle.write(json.dumps({"run_id": "RUN-0022", "event": "completed", "result": report}, default=str) + "\n")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__": main()
