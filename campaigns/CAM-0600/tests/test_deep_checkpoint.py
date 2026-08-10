from __future__ import annotations
from pathlib import Path
import json
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


def test_split_repair_semantics_and_all_25_runs_reconcile() -> None:
    for n in range(600,625):
        report=json.loads((ROOT/"campaigns"/f"CAM-{n:04d}"/"artifacts"/"RUN-0020"/"execution_report.json").read_text(encoding="utf-8"))
        assert report["status"]=="completed"
        assert report["maximum_loaded_date"]=="2026-04-30"
        assert report["holdout_rows_loaded"]==0
        assert report["semantic_fixtures"]["forward_split_adjustment"]=="passed"
        run=yaml.safe_load((ROOT/"campaigns"/f"CAM-{n:04d}"/"runs"/"RUN-0020.yaml").read_text(encoding="utf-8"))
        assert run["status"]=="completed"
        assert run["result"]["executed_variant_cost_count"]==report["executed_variant_cost_count"]


def test_repaired_quote_replay_has_coverage_and_monotone_cost_decay() -> None:
    metrics=pd.read_csv(SH/"split_repaired_quote_metrics_RUN-0023.csv")
    central=metrics[(metrics.clock.astype(str).str.zfill(4)=="0940") & (metrics.extra_slippage_bps_per_side==2)]
    assert central.campaign_id.nunique()==23
    assert central.role_coverage.min()>=0.999
    assert (central.net_simple_return>0).all()
    for _,group in metrics.groupby(["campaign_id","clock"]):
        ordered=group.sort_values("extra_slippage_bps_per_side")
        assert ordered.net_simple_return.is_monotonic_decreasing


def test_final_repaired_ensemble_is_unpromoted_and_holdout_free() -> None:
    results=yaml.safe_load((ROOT/"campaigns"/"CAM-0625"/"RESULTS.yaml").read_text(encoding="utf-8"))
    assert results["promotion_ready"] is False
    assert results["holdout_rows_loaded"]==0
    assert results["prior_runs_status"]=="RUN-0001_through_RUN-0016_invalid_split_adjustment"
    assert results["quote_0940_2bps_extra"]["positive_months"]==10
    assert results["quote_0940_2bps_extra"]["negative_months"]==2
    pseudo=yaml.safe_load((ROOT/"campaigns"/"CAM-0625"/"runs"/"RUN-0018.yaml").read_text(encoding="utf-8"))
    assert pseudo["status"]=="completed_no_candidate"
    assert pseudo["result"]["holdout_rows_loaded"]==0


def test_recent_leaders_are_not_misclassified_as_independent_printers() -> None:
    audit=json.loads((ROOT/"campaigns"/"CAM-0625"/"artifacts"/"RUN-0025"/"execution_report.json").read_text(encoding="utf-8"))
    assert all(item["eligible_variants"]==0 for item in audit["early_gate"])
    corr=audit["full_daily_correlations"]
    assert corr["CAM-0611"]["CAM-0612"]>0.85
    attribution=json.loads((ROOT/"campaigns"/"CAM-0625"/"artifacts"/"RUN-0027"/"execution_report.json").read_text(encoding="utf-8"))
    assert attribution["holdout_rows_loaded"]==0
    assert attribution["positive_symbols"]>=30
    assert attribution["leave_top5_symbols_out_net"]>0.15


def test_symbol_cap_is_not_claimed_to_solve_concentration() -> None:
    run=yaml.safe_load((ROOT/"campaigns"/"CAM-0625"/"runs"/"RUN-0028.yaml").read_text(encoding="utf-8"))
    variants={str(x["cap"]):x for x in run["result"]["variants"]}
    assert variants["0.1"]["quote"]["maximum_drawdown"]<variants["uncapped"]["quote"]["maximum_drawdown"]
    improvement=variants["uncapped"]["quote_top5_symbol_positive_share"]-variants["0.1"]["quote_top5_symbol_positive_share"]
    assert improvement<0.02
    results=yaml.safe_load((ROOT/"campaigns"/"CAM-0625"/"RESULTS.yaml").read_text(encoding="utf-8"))
    assert results["symbol_cap_test"]["decision"]=="reject_as_primary_because_concentration_improvement_is_not_material"
