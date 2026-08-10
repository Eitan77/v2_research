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
OUT = CAM / "CAM-0606" / "artifacts" / "RUN-0022"
sys.path.insert(0, str(CAM / "CAM-0600" / "src"))
from suite_core import load_panels

PAIRS = [("QQQ", "SPY"), ("SMH", "XLK"), ("IWM", "SPY"), ("XLY", "XLP"), ("XLE", "USO"), ("TLT", "SHY")]
FOLDS = [("2019_2021", "2019-01-01", "2021-12-31"), ("2022_2023", "2022-01-01", "2023-12-31"), ("2024_2026apr", "2024-01-01", "2026-04-30")]


def drawdown(pnl: pd.Series) -> float:
    equity = 1.0 + pnl.cumsum()
    return float(((equity.cummax() - equity) / equity.cummax()).max())


def metrics(pnl: pd.Series, active: pd.Series, stop_hits: pd.Series) -> dict:
    monthly = pnl.groupby(pnl.index.to_period("M")).sum()
    positive_days = pnl.clip(lower=0).sort_values(ascending=False)
    positive_total = float(positive_days.sum())
    folds = {}
    for name, start, end in FOLDS:
        mask = (pnl.index >= start) & (pnl.index <= end)
        folds[name] = float(pnl[mask].sum())
    return {
        "net_simple_return": float(pnl.sum()),
        "maximum_drawdown": drawdown(pnl),
        "positive_months": int((monthly > 0).sum()),
        "negative_months": int((monthly < 0).sum()),
        "inactive_months": int((monthly == 0).sum()),
        "recent12_positive_months": int((monthly.tail(12) > 0).sum()),
        "recent12_net": float(monthly.tail(12).sum()),
        "active_pair_days": int(active.sum()),
        "short_stop_hits": int(stop_hits.sum()),
        "top5_positive_day_share": float(positive_days.head(5).sum() / positive_total) if positive_total > 0 else None,
        "folds": folds,
        "worst_fold": float(min(folds.values())),
    }


def pair_inputs(panel, a: str, b: str, lookback: int, z_window: int, corr_window: int = 126):
    ca, cb = panel.symbol_to_col[a], panel.symbol_to_col[b]
    tri_a, tri_b = panel.total_return_index[:, ca], panel.total_return_index[:, cb]
    relative = np.full(panel.n_dates, np.nan)
    valid = np.isfinite(tri_a[lookback:]) & np.isfinite(tri_a[:-lookback]) & np.isfinite(tri_b[lookback:]) & np.isfinite(tri_b[:-lookback]) & (tri_a[:-lookback] > 0) & (tri_b[:-lookback] > 0)
    rel = np.full(panel.n_dates - lookback, np.nan)
    rel[valid] = np.log(tri_a[lookback:][valid] / tri_a[:-lookback][valid]) - np.log(tri_b[lookback:][valid] / tri_b[:-lookback][valid])
    relative[lookback:] = rel
    series = pd.Series(relative)
    mean = series.rolling(z_window, min_periods=z_window).mean().to_numpy()
    std = series.rolling(z_window, min_periods=z_window).std(ddof=1).to_numpy()
    z = np.where(std > 0, (relative - mean) / std, np.nan)
    close_a = pd.Series(tri_a).pct_change(fill_method=None)
    close_b = pd.Series(tri_b).pct_change(fill_method=None)
    corr = close_a.rolling(corr_window, min_periods=int(corr_window * 0.8)).corr(close_b).to_numpy()
    return ca, cb, z, corr


def pair_trade(panel, pair, lookback, z_window, threshold, min_corr, short_stop):
    a, b = pair
    ca, cb, z, corr = pair_inputs(panel, a, b, lookback, z_window)
    n = panel.n_dates
    gross = np.zeros(n)
    active = np.zeros(n, dtype=int)
    stop_hit = np.zeros(n, dtype=int)
    abs_z = np.zeros(n)
    direction = np.zeros(n, dtype=int)
    for i in range(1, n):
        signal_z = z[i - 1]
        signal_corr = corr[i - 1]
        if not np.isfinite(signal_z) or not np.isfinite(signal_corr) or signal_corr < min_corr or abs(signal_z) < threshold:
            continue
        rich, cheap = (ca, cb) if signal_z > 0 else (cb, ca)
        needed = [panel.adj_open[i, rich], panel.adj_high[i, rich], panel.adj_close[i, rich], panel.adj_open[i, cheap], panel.adj_close[i, cheap]]
        if not all(np.isfinite(v) and v > 0 for v in needed):
            continue
        long_ret = panel.adj_close[i, cheap] / panel.adj_open[i, cheap] - 1.0
        short_adverse = panel.adj_high[i, rich] / panel.adj_open[i, rich] - 1.0
        if short_adverse >= short_stop:
            short_ret = -(short_stop + 0.0005)
            stop_hit[i] = 1
        else:
            short_ret = -(panel.adj_close[i, rich] / panel.adj_open[i, rich] - 1.0)
        gross[i] = 0.5 * (long_ret + short_ret)
        active[i] = 1
        abs_z[i] = abs(signal_z)
        direction[i] = 1 if signal_z > 0 else -1
    return {"gross": gross, "active": active, "stop_hit": stop_hit, "abs_z": abs_z, "direction": direction}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    panel = load_panels()["etf"]
    dates = pd.DatetimeIndex(panel.dates)
    rows = []
    daily_by_id = {}
    for lookback, z_window, threshold, min_corr, short_stop in itertools.product((1, 5, 20), (63, 126), (1.0, 1.5), (0.5, 0.7), (0.02, 0.04)):
        pair_data = {pair: pair_trade(panel, pair, lookback, z_window, threshold, min_corr, short_stop) for pair in PAIRS}
        expressions = [(f"pair_{a}_{b}", [(a, b)]) for a, b in PAIRS] + [("equal_active_pairs", PAIRS), ("largest_dislocation_pair", PAIRS)]
        for expression, included in expressions:
            gross = np.zeros(panel.n_dates)
            active_pair_days = np.zeros(panel.n_dates, dtype=int)
            stop_hits = np.zeros(panel.n_dates, dtype=int)
            for i in range(panel.n_dates):
                candidates = [(pair, pair_data[pair]) for pair in included if pair_data[pair]["active"][i]]
                if not candidates:
                    continue
                if expression == "largest_dislocation_pair":
                    candidates = [max(candidates, key=lambda item: item[1]["abs_z"][i])]
                gross[i] = float(np.mean([item[1]["gross"][i] for item in candidates]))
                active_pair_days[i] = len(candidates)
                stop_hits[i] = sum(item[1]["stop_hit"][i] for item in candidates)
            variant_id = f"{expression}__r{lookback}__zw{z_window}__z{threshold:g}__corr{min_corr:g}__stop{int(short_stop*100)}"
            result = {"variant_id": variant_id, "expression": expression, "lookback": lookback, "z_window": z_window, "entry_z": threshold, "min_corr": min_corr, "short_stop": short_stop}
            for cost_bps in (2, 10):
                net = gross - (active_pair_days > 0) * (2 * cost_bps / 10000.0)
                m = metrics(pd.Series(net, index=dates), pd.Series(active_pair_days, index=dates), pd.Series(stop_hits, index=dates))
                for key, value in m.items():
                    if key == "folds":
                        for fold, fold_value in value.items():
                            result[f"cost{cost_bps}_{fold}"] = fold_value
                    else:
                        result[f"cost{cost_bps}_{key}"] = value
                if cost_bps == 2:
                    daily_by_id[variant_id] = pd.DataFrame({"date": dates, "gross_pnl": gross, "net_pnl": net, "active_pairs": active_pair_days, "short_stop_hits": stop_hits})
            rows.append(result)
    frame = pd.DataFrame(rows)
    gate = (
        frame.cost2_net_simple_return.gt(0)
        & frame.cost10_net_simple_return.gt(0)
        & frame.cost2_maximum_drawdown.le(0.20)
        & frame.cost2_worst_fold.gt(0)
        & frame.cost2_recent12_positive_months.ge(7)
        & frame.cost2_active_pair_days.ge(100)
    )
    survivors = frame[gate].sort_values(["cost2_worst_fold", "cost2_net_simple_return"], ascending=False)
    selected = survivors.iloc[0] if len(survivors) else None
    report = {
        "status": "completed" if selected is not None else "completed_no_candidate",
        "run_id": "RUN-0022",
        "variants": int(len(frame)),
        "structured_survivors": int(gate.sum()),
        "selected_variant": None if selected is None else str(selected.variant_id),
        "selected_metrics": None if selected is None else {k: (float(v) if isinstance(v, (float, np.floating)) else int(v) if isinstance(v, (int, np.integer)) else v) for k, v in selected.to_dict().items()},
        "best_raw_2bps": frame.nlargest(1, "cost2_net_simple_return").iloc[0].to_dict(),
        "maximum_loaded_date": str(dates.max().date()),
        "holdout_rows_loaded": int((dates >= "2026-05-01").sum()),
        "maximum_gross_exposure": 1.0,
        "broker_margin": False,
        "direct_short_overnight": False,
        "interpretation": "Actual long-cheap/short-rich source identity with forced same-day close and predefined short stop; daily bars cannot model queue or intrabar stop slippage beyond the frozen 5 bp penalty.",
    }
    if report["holdout_rows_loaded"] != 0:
        raise RuntimeError("sealed holdout loaded")
    frame.to_csv(OUT / "variant_metrics.csv", index=False)
    if selected is not None:
        daily_by_id[str(selected.variant_id)].to_parquet(OUT / "selected_daily.parquet", index=False)
    (OUT / "execution_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    path = CAM / "CAM-0606" / "runs" / "RUN-0022.yaml"
    run = yaml.safe_load(path.read_text(encoding="utf-8"))
    run["status"] = report["status"]
    run["result"] = json.loads(json.dumps(report, default=str))
    run["decision"] = "Advance only a structured survivor to quote-role replay; otherwise retain retirement with the identity gap closed."
    path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    with (CAM / "CAM-0606" / "WORKLOG.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"run_id": "RUN-0022", "event": "completed", "result": report}, default=str) + "\n")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
