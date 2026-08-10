from __future__ import annotations
from pathlib import Path
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]
SH=ROOT/"campaigns"/"CAM-0600"/"artifacts"/"shared"


def test_valid_quote_replay_uses_raw_midpoint_reference() -> None:
    replay=pd.read_parquet(SH/"target_change_replay_0940.parquet")
    complete=replay[replay.effective_complete]
    midpoint=(complete.bid_price+complete.ask_price)/2
    ratio=midpoint/complete.reference_mid
    assert complete.reference_mid.gt(0).all()
    assert ratio.quantile(.999)<1.25
    assert ratio.quantile(.001)>0.80
    assert (SH/"target_change_quote_metrics_INVALID_SPLIT_REFERENCE.csv").exists()


def test_quote_cost_stress_is_monotone() -> None:
    m=pd.read_csv(SH/"target_change_quote_metrics.csv")
    for _,g in m.groupby(["campaign_id","clock"]):
        x=g.sort_values("extra_slippage_bps_per_side")
        assert x.net_simple_return.is_monotonic_decreasing


def test_ensemble_execution_stress_is_monotone_and_holdout_free() -> None:
    p=ROOT/"campaigns"/"CAM-0625"/"artifacts"/"RUN-0003"
    m=pd.read_csv(p/"stress_metrics.csv")
    all_sleeves=m[m.sleeves.eq("all")]
    for _,g in all_sleeves.groupby(["clock","rule"]):
        assert g.sort_values("extra_slippage_bps_per_side").net_simple_return.is_monotonic_decreasing
    report=__import__("json").loads((p/"execution_report.json").read_text())
    assert report["holdout_rows_loaded"]==0
    assert report["maximum_loaded_date"]=="2026-04-30"


def test_all_deep_checkpoint_results_keep_holdout_sealed() -> None:
    for n in range(600,626):
        p=ROOT/"campaigns"/f"CAM-{n:04d}"/"RESULTS.yaml"
        data=yaml.safe_load(p.read_text(encoding="utf-8"))
        checkpoint=data.get("deep_development_checkpoint",data)
        assert checkpoint["holdout_rows_loaded"]==0
        assert checkpoint["maximum_loaded_date"]=="2026-04-30"
        assert checkpoint["promotion_ready"] is False
