from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "campaigns" / "CAM-0600" / "src"
sys.path.insert(0, str(SRC))

from baseline_strategies import eligible, moving_average
from deep_strategies import liquid_mask, trailing_return
from suite_core import CAMPAIGNS, evaluate_weights, load_panels

OUT = CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0038"
RUN = CAMPAIGNS / "CAM-0600" / "runs" / "RUN-0038.yaml"


def smooth(values, window):
    if window == 1:
        return values
    frame = pd.DataFrame(values)
    return frame.rolling(window, min_periods=window).mean().to_numpy()


def build(panel, threshold, smoothing, breadth_gate):
    score = smooth(trailing_return(panel, 126, 21), smoothing)
    ma = moving_average(panel, 200)
    base_eligible = eligible(panel) & liquid_mask(panel, 0.5)
    mask = base_eligible & (panel.adj_close > ma)
    breadth = np.divide(mask.sum(1), base_eligible.sum(1), out=np.zeros(panel.n_dates), where=base_eligible.sum(1) > 0)
    log_return = np.log(panel.adj_close / np.vstack([np.full((1, panel.n_symbols), np.nan), panel.adj_close[:-1]]))
    out = np.zeros_like(score)
    for i in range(200, panel.n_dates):
        if breadth_gate is not None and breadth[i] < breadth_gate:
            continue
        columns = np.flatnonzero(mask[i] & np.isfinite(score[i]))
        columns = columns[np.argsort(score[i, columns], kind="stable")[::-1]][:50]
        chosen = []
        for column in columns:
            if not chosen:
                chosen.append(column)
            else:
                x = log_return[i - 63:i, column]
                y = log_return[i - 63:i, chosen]
                correlations = []
                for k in range(len(chosen)):
                    valid = np.isfinite(x) & np.isfinite(y[:, k])
                    correlations.append(np.corrcoef(x[valid], y[valid, k])[0, 1] if valid.sum() >= 40 else 1.0)
                if np.nanmax(correlations) < threshold:
                    chosen.append(column)
            if len(chosen) == 10:
                break
        if chosen:
            out[i, chosen] = 1 / len(chosen)
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    panel = load_panels()["sp500"]
    rows = []
    for threshold in (0.7, 0.8, 0.9):
        for smoothing in (1, 3, 5):
            for breadth_gate in (None, 0.5, 0.6):
                weights = build(panel, threshold, smoothing, breadth_gate)
                variant = f"corr{threshold:.1f}_smooth{smoothing}_breadth{breadth_gate or 'none'}"
                for cost in (2.0, 5.0, 10.0):
                    metrics, *_ = evaluate_weights(panel, weights, cost, holding="open_to_next_open", execution_lag=1)
                    rows.append({"variant": variant, "threshold": threshold, "smoothing": smoothing, "breadth_gate": breadth_gate, **metrics})
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / "variant_metrics.csv", index=False)
    at2 = frame[frame.cost_bps_per_side == 2].copy()
    at10 = frame[frame.cost_bps_per_side == 10][["variant", "net_simple_return"]].rename(columns={"net_simple_return": "net_10bps"})
    at2 = at2.merge(at10, on="variant", validate="one_to_one")
    at2["score"] = at2.net_simple_return - at2.maximum_drawdown + 3 * at2.recent12_average_month + 0.02 * at2.recent12_positive_months
    at2.to_csv(OUT / "comparison_2bps.csv", index=False)
    selected = at2[(at2.net_10bps > 0) & (at2.recent12_positive_months >= 9)].sort_values("score", ascending=False).iloc[0].to_dict()
    report = {
        "status": "completed",
        "run_id": "RUN-0038",
        "selected": selected,
        "profitable_at_10bps": int((at2.net_10bps > 0).sum()),
        "neighborhood_variants": int(len(at2)),
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "broker_margin": False,
    }
    report = json.loads(json.dumps(report, default=lambda x: x.item() if isinstance(x, np.generic) else str(x)))
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n")
    record = yaml.safe_load(RUN.read_text())
    record["status"] = "completed"
    record["result"] = report
    record["decision"] = "Quote replay only if smoothing improves the uncapped/corr-capped benchmark without sparse cadence or higher contributor dependence."
    RUN.write_text(yaml.safe_dump(record, sort_keys=False))
    print(at2.sort_values("score", ascending=False).head(12)[["variant", "net_simple_return", "net_10bps", "maximum_drawdown", "recent12_average_month", "recent12_positive_months", "top5_symbol_positive_share", "position_change_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
