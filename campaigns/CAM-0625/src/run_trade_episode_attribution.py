from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
CAM = ROOT / "campaigns"
SHARED = CAM / "CAM-0600" / "artifacts" / "shared"
OUT = CAM / "CAM-0625" / "artifacts" / "RUN-0032"
IDS = ["CAM-0600", "CAM-0621", "CAM-0624", "CAM-0618"]
sys.path.insert(0, str(CAM / "CAM-0600" / "src"))

from deep_strategies import build_deep_variants
from repair_strategies import build_repair_variants
from run_suite import _load_or_build_fundamentals
from suite_core import load_panels


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = pd.read_csv(SHARED / "split_repaired_diagnostic_summary.csv").set_index("campaign_id")
    repair = pd.read_csv(SHARED / "split_repaired_repair_diagnostic_summary.csv").set_index("campaign_id")
    replay = pd.read_parquet(SHARED / "split_repaired_quote_replay_0940.parquet")
    replay["session_date"] = pd.to_datetime(replay.session_date)
    panels = load_panels()
    fundamentals, _ = _load_or_build_fundamentals(panels)
    rows = []
    for campaign_id in IDS:
        selected_source = repair if campaign_id == "CAM-0621" else base
        variant_id = str(selected_source.loc[campaign_id, "selected_variant"])
        builder = build_repair_variants if campaign_id == "CAM-0621" else build_deep_variants
        variant = next(v for v in builder(campaign_id, panels, fundamentals) if v.variant_id == variant_id)
        executed = np.zeros_like(variant.weights)
        executed[1:] = variant.weights[:-1]
        returns = variant.panel.open_to_next_open_return.copy()
        returns[-1] = variant.panel.open_to_close_return[-1]
        dates = pd.DatetimeIndex(variant.panel.dates)
        mask = (dates >= "2025-05-01") & (dates <= "2026-04-30")
        costs = replay[(replay.campaign_id == campaign_id) & replay.effective_complete].copy()
        buy = costs.side.eq("buy")
        costs["execution_adjustment"] = np.where(
            buy,
            costs.delta_weight * (costs.ask_price / costs.reference_mid - 1),
            costs.delta_weight * (1 - costs.bid_price / costs.reference_mid),
        ) + costs.delta_weight * 2 / 10000
        cost_map = costs.groupby(["session_date", "symbol"]).execution_adjustment.sum()
        for column, symbol in enumerate(variant.panel.symbols.astype(str)):
            frame = pd.DataFrame({
                "date": dates[mask],
                "executed_weight": executed[mask, column],
                "gross_pnl": executed[mask, column] * np.nan_to_num(returns[mask, column], nan=0.0),
            })
            frame["campaign_id"] = campaign_id
            frame["symbol"] = symbol
            frame["execution_adjustment"] = [cost_map.get((date, symbol), 0.0) for date in frame.date]
            frame["active"] = frame.executed_weight.abs() > 1e-12
            frame["episode_id"] = (frame.active & ~frame.active.shift(fill_value=False)).cumsum()
            frame = frame[frame.active | frame.execution_adjustment.ne(0)].copy()
            if len(frame):
                frame["weighted_gross_pnl"] = frame.gross_pnl / len(IDS)
                frame["weighted_execution_adjustment"] = frame.execution_adjustment / len(IDS)
                frame["weighted_net_pnl"] = frame.weighted_gross_pnl - frame.weighted_execution_adjustment
                rows.append(frame)
    detail = pd.concat(rows, ignore_index=True)
    episodes = detail.groupby(["campaign_id", "symbol", "episode_id"], as_index=False).agg(
        start=("date", "min"), end=("date", "max"), holding_days=("active", "sum"),
        gross_pnl=("weighted_gross_pnl", "sum"),
        execution_adjustment=("weighted_execution_adjustment", "sum"),
        net_pnl=("weighted_net_pnl", "sum"),
    ).sort_values("net_pnl", ascending=False)
    total = float(episodes.net_pnl.sum())
    expected = 0.39962722208704066
    if abs(total - expected) > 1e-8:
        raise RuntimeError(f"episode attribution mismatch {total} != {expected}")
    positive = episodes.net_pnl.clip(lower=0)
    report = {
        "status": "completed",
        "run_id": "RUN-0032",
        "net_simple_return": total,
        "episodes": int(len(episodes)),
        "positive_episodes": int((episodes.net_pnl > 0).sum()),
        "negative_episodes": int((episodes.net_pnl < 0).sum()),
        "win_rate": float((episodes.net_pnl > 0).mean()),
        "median_holding_days": float(episodes.holding_days.median()),
        "top_episode_positive_share": float(positive.iloc[0] / positive.sum()),
        "top5_episode_positive_share": float(positive.head(5).sum() / positive.sum()),
        "top10_episode_positive_share": float(positive.head(10).sum() / positive.sum()),
        "leave_top_episode_out_net": float(total - episodes.net_pnl.iloc[0]),
        "leave_top5_episodes_out_net": float(total - episodes.net_pnl.head(5).sum()),
        "leave_top10_episodes_out_net": float(total - episodes.net_pnl.head(10).sum()),
        "top10": episodes.head(10).to_dict("records"),
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "interpretation": "Execution-adjusted contiguous campaign-symbol exposure episodes; overlapping sleeves are separate episodes.",
    }
    serializable_report = json.loads(json.dumps(report, default=str))
    episodes.to_csv(OUT / "trade_episode_attribution.csv", index=False)
    detail.to_parquet(OUT / "trade_episode_daily_detail.parquet", index=False)
    (OUT / "execution_report.json").write_text(json.dumps(serializable_report, indent=2) + "\n", encoding="utf-8")
    path = CAM / "CAM-0625" / "runs" / "RUN-0032.yaml"
    run = yaml.safe_load(path.read_text(encoding="utf-8"))
    run["status"] = "completed"
    run["result"] = serializable_report
    run["decision"] = "Use episode concentration as a hard qualification of the recent result; no parameter change or promotion."
    path.write_text(yaml.safe_dump(run, sort_keys=False), encoding="utf-8")
    with (CAM / "CAM-0625" / "WORKLOG.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"run_id": "RUN-0032", "event": "completed", "result": serializable_report}) + "\n")
    print(json.dumps(serializable_report, indent=2))


if __name__ == "__main__":
    main()
