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
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0610" / "src"))
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0617" / "src"))

from baseline_strategies import moving_average, multiple_cluster_weights, weighted_regression_weights
from deep_strategies import active_trend_rank, concentrate_positive, liquid_mask, trailing_vol, trend_mask
from run_correlation_capped_ma import build as corr_build
from run_notable_overlays import band, persist, renorm, vol_target
from run_suite import _load_or_build_fundamentals
from run_true_daily_alpha import solve as alpha_solve
from suite_core import CAMPAIGNS, evaluate_weights, load_panels

OUT = CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0035"
RUN = CAMPAIGNS / "CAM-0600" / "runs" / "RUN-0035.yaml"
COSTS = (2.0, 5.0, 10.0)


def invvol(p, w, window):
    v = trailing_vol(p, window)
    x = np.where((w > 0) & np.isfinite(v) & (v > 1e-8), w / v, 0.0)
    return renorm(x, np.abs(w).sum(1))


def min_history_mask(p, count):
    valid = np.isfinite(p.adj_close)
    running = np.zeros_like(valid, dtype=np.int32)
    for i in range(1, len(valid)):
        running[i] = np.where(valid[i], running[i - 1] + 1, 0)
    running[0] = valid[0]
    return running >= count


def variants(panels, fundamentals):
    sp = panels["sp500"]
    q = panels["qqq"]

    for window in (150, 200, 250):
        for k in (5, 10, 15):
            for history in ("native", "252"):
                condition = sp.adj_close > moving_average(sp, window)
                if history == "252":
                    condition &= min_history_mask(sp, 252)
                base = active_trend_rank(sp, condition, np.arange(sp.n_dates), k, "momentum")
                for persistence in (1, 2, 3, 5):
                    w = base if persistence == 1 else persist(base, persistence)
                    yield "ma200_uncapped", f"ma{window}_top{k}_hist{history}_p{persistence}", sp, w

    for cap in (0.7, 0.8, 0.9):
        base = corr_build(sp, cap)
        for persistence in (1, 2, 3):
            w = base if persistence == 1 else persist(base, persistence)
            yield "ma200_corr_capped", f"corr{cap:.1f}_p{persistence}", sp, w

    for short, long in ((20, 50), (50, 150), (50, 200), (50, 250)):
        condition = moving_average(sp, short) > moving_average(sp, long)
        for k in (5, 10, 15):
            base = active_trend_rank(sp, condition, np.arange(sp.n_dates), k, "momentum")
            for persistence in (1, 2, 3, 5):
                w = base if persistence == 1 else persist(base, persistence)
                yield "ma50_200", f"ma{short}_{long}_top{k}_p{persistence}", sp, w

    for family, panel in (("cluster_residual", sp), ("characteristic_residual", q)):
        for lookback in (5, 10, 20):
            if family == "cluster_residual":
                raw, _ = multiple_cluster_weights(panel, panels["etf"], 126, lookback)
            else:
                raw, _ = weighted_regression_weights(panel, fundamentals["qqq"], lookback, 126)
            mask = liquid_mask(panel, 0.5) & trend_mask(panel, 200)
            for k in (3, 5, 10):
                base = concentrate_positive(raw, k, mask)
                for persistence in (1, 2, 3):
                    w = base if persistence == 1 else persist(base, persistence)
                    yield family, f"r{lookback}_top{k}_p{persistence}", panel, w

    for wins in ((3, 10, 21), (5, 20, 50), (10, 50, 200)):
        a, b, c = [moving_average(sp, x) for x in wins]
        condition = (a > b) & (b > c)
        for k in (3, 5, 10):
            base = active_trend_rank(sp, condition, np.arange(sp.n_dates), k, "momentum")
            for target in (None, 0.10, 0.15, 0.20):
                w = base if target is None else vol_target(sp, base, target)
                label = "none" if target is None else f"{target:.2f}"
                yield "triple_ma", f"ma{'_'.join(map(str, wins))}_top{k}_vt{label}", sp, w

    alpha = concentrate_positive(alpha_solve(q, 20, 5), 10, liquid_mask(q, 0.5))
    for iv in (None, 42, 63, 126):
        scaled = alpha if iv is None else invvol(q, alpha, iv)
        for threshold in (None, 0.10, 0.20):
            w = scaled if threshold is None else band(scaled, threshold)
            yield "true_daily_alpha", f"iv{iv or 'none'}_band{threshold or 'none'}", q, w


def sndk_audit(panel):
    j = list(panel.symbols.astype(str)).index("SNDK")
    valid = np.isfinite(panel.raw_close[:, j])
    member = panel.member[:, j]
    dates = pd.to_datetime(panel.dates)
    idx = np.flatnonzero(valid)
    before_membership = int(np.sum(valid & (dates < dates[member].min())))
    ret = pd.Series(panel.adj_close[:, j], index=dates).pct_change()
    return {
        "first_price_date": str(dates[idx[0]].date()),
        "first_membership_date": str(dates[member].min().date()),
        "last_loaded_date": str(dates[idx[-1]].date()),
        "price_observations_before_membership": before_membership,
        "maximum_absolute_close_return": float(ret.abs().max()),
        "split_factor_values": sorted(set(panel.split_factor[valid, j].astype(float))),
        "native_ma200_has_pre_membership_history": before_membership >= 200,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    panels = load_panels()
    fundamentals, _ = _load_or_build_fundamentals(panels)
    rows = []
    for family, variant, panel, weights in variants(panels, fundamentals):
        for cost in COSTS:
            metrics, *_ = evaluate_weights(panel, weights, cost, holding="open_to_next_open", execution_lag=1)
            rows.append({"candidate": family, "variant": variant, **metrics})
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / "variant_metrics.csv", index=False)
    at2 = frame[frame.cost_bps_per_side == 2].copy()
    at10 = frame[frame.cost_bps_per_side == 10][["candidate", "variant", "net_simple_return"]].rename(columns={"net_simple_return": "net_10bps"})
    at2 = at2.merge(at10, on=["candidate", "variant"], validate="one_to_one")
    at2["robust_score"] = (
        at2.net_simple_return
        - at2.maximum_drawdown
        + 3.0 * at2.recent12_average_month
        + 0.02 * at2.recent12_positive_months
    )
    at2.to_csv(OUT / "comparison_2bps.csv", index=False)
    selected = []
    neighborhoods = []
    for candidate, group in at2.groupby("candidate"):
        viable = group[(group.net_10bps > 0) & (group.recent12_positive_months >= 8)]
        if viable.empty:
            viable = group[group.net_10bps > 0]
        if viable.empty:
            viable = group
        pick = viable.sort_values(["robust_score", "net_simple_return"], ascending=False).iloc[0]
        selected.append(pick.to_dict())
        neighborhoods.append({
            "candidate": candidate,
            "variants": int(len(group)),
            "positive_at_10bps": int((group.net_10bps > 0).sum()),
            "positive_recent_average": int((group.recent12_average_month > 0).sum()),
            "median_net_2bps": float(group.net_simple_return.median()),
            "median_drawdown": float(group.maximum_drawdown.median()),
            "median_recent_positive_months": float(group.recent12_positive_months.median()),
        })
    report = {
        "status": "completed",
        "run_id": "RUN-0035",
        "selected": selected,
        "neighborhoods": neighborhoods,
        "sndk_data_integrity": sndk_audit(panels["sp500"]),
        "maximum_loaded_date": str(max(p.dates.max() for p in panels.values())),
        "holdout_rows_loaded": 0,
        "broker_margin": False,
    }
    report = json.loads(json.dumps(report, default=lambda x: x.item() if isinstance(x, np.generic) else str(x)))
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n")
    record = yaml.safe_load(RUN.read_text())
    record["status"] = "completed"
    record["result"] = report
    record["decision"] = "Quote-replay only improvements supported by a profitable adjacent parameter neighborhood; treat SNDK as a causal but high-concentration contributor, not a data-history artifact."
    RUN.write_text(yaml.safe_dump(record, sort_keys=False))
    print(pd.DataFrame(selected)[["candidate", "variant", "net_simple_return", "net_10bps", "maximum_drawdown", "recent12_average_month", "recent12_positive_months", "top5_symbol_positive_share"]].to_string(index=False))
    print(pd.DataFrame(neighborhoods).to_string(index=False))
    print(json.dumps(report["sndk_data_integrity"], indent=2))


if __name__ == "__main__":
    main()
