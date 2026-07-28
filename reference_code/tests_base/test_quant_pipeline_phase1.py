import warnings

import numpy as np
import pandas as pd

from quant_pipeline.config import ScanConfig
from quant_pipeline.bulk_scan import _clustered_inference_from_moments, _pair_moments_stable
from quant_pipeline.features import build_features
from quant_pipeline.gpu import CorrelationBackend
from quant_pipeline.registry import feature_registry, target_registry
from quant_pipeline.run import _classify_detailed_candidates
from quant_pipeline.scanner import _clustered_slope, scan
from quant_pipeline.table import add_targets, validate_point_in_time


def fixture_bars(days=4):
    parts=[]
    for day in pd.bdate_range("2026-04-01", periods=days):
        ts=pd.date_range(pd.Timestamp(day.date()).tz_localize("America/New_York")+pd.Timedelta(hours=9,minutes=30),periods=78,freq="5min").tz_convert("UTC")
        for j,symbol in enumerate(["AAA","BBB","QQQ"]):
            z=np.arange(78); close=100+j+z*.01+np.sin(z/7+j)*.1
            parts.append(pd.DataFrame({"symbol":symbol,"bar_start_ts":ts,"bar_end_ts":ts+pd.Timedelta(minutes=5),"available_at_ts":ts+pd.Timedelta(minutes=5),"open":close-.01,"high":close+.05,"low":close-.05,"close":close,"volume":1000+z*7+j,"vwap":close,"session_date":day.date()}))
    return pd.concat(parts,ignore_index=True)


def test_full_core_registry_builds_and_accounts_for_skips():
    cfg=ScanConfig(lookbacks=[1,2,3,5,10])
    specs=feature_registry(cfg.lookbacks)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
        frame,built=build_features(fixture_bars(),cfg,specs)
    assert len(specs)>=290
    skipped={s.name for s in specs}-{s.name for s in built}
    assert {"sector_return","stock_minus_sector_return","sector_rank","sector_breadth_positive"}.issubset(skipped)
    assert all(("_1m" in name or "_3m" in name or name.startswith("sector") or name=="stock_minus_sector_return") for name in skipped)
    frame=add_targets(frame,target_registry(),"QQQ")
    assert validate_point_in_time(frame,target_registry())["target_timing_violations"]==0
    assert frame.filter(like="benchmark_adjusted").shape[1]==len(target_registry())//2


def test_signed_features_do_not_register_unstable_mean_ratios():
    names={spec.name for spec in feature_registry([1,2,3,5,10])}
    assert "return_1_mean_ratio_1560" not in names
    assert "distance_session_vwap_mean_ratio_4680" not in names
    assert "range_position_10_mean_ratio_1560" in names


def test_scanner_outputs_fdr_ranking_and_cuda_backend(tmp_path):
    cfg=ScanConfig(lookbacks=[1,2,3,5,10],min_observations=100,min_sessions=2,min_symbols=2,min_bin_observations=5,quantiles=5,checkpoint_every_pairs=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        frame,built=build_features(fixture_bars(),cfg,feature_registry(cfg.lookbacks))
    frame=add_targets(frame,target_registry(),"QQQ")
    wanted=[s for s in built if s.name in {"return_1","relative_volume_5","return_rank_5"}]
    result,_=scan(frame,wanted,["fwd_return_5m","fwd_return_15m"],cfg,tmp_path/"pairs.csv")
    assert len(result)==6
    assert {"bh_fdr_p","anomaly_score","redundancy_group","cluster_t"}.issubset(result.columns)
    assert (tmp_path/"pairs.csv").exists()
    assert CorrelationBackend(True).name.startswith("torch:")


def test_batched_clustered_inference_matches_exact_pair_regressions():
    import torch

    rng=np.random.default_rng(12); sessions=np.repeat(np.arange(30),20)
    session_shock=np.repeat(rng.normal(0,.8,30),20)
    x=np.column_stack([rng.normal(size=len(sessions)),rng.normal(size=len(sessions))])
    y=np.column_stack([.4*x[:,0]+session_shock+rng.normal(size=len(sessions)),
                       -.25*x[:,1]+.5*session_shock+rng.normal(size=len(sessions))])
    x[::17,1]=np.nan; y[::23,0]=np.nan
    tx=torch.as_tensor(x,dtype=torch.float64); ty=torch.as_tensor(y,dtype=torch.float64)
    corr,n,mx,my,vx,vy,cov=_pair_moments_stable(tx,ty,feature_block=2)
    se,t=_clustered_inference_from_moments(
        tx,ty,torch.as_tensor(sessions,dtype=torch.long),n,mx,my,vx,cov,feature_block=2
    )
    for i in range(x.shape[1]):
        for j in range(y.shape[1]):
            valid=np.isfinite(x[:,i])&np.isfinite(y[:,j])
            _,expected_se,expected_t,_=_clustered_slope(x[valid,i],y[valid,j],sessions[valid])
            assert np.isclose(se[i,j].item(),expected_se,rtol=1e-10,atol=1e-12)
            assert np.isclose(t[i,j].item(),expected_t,rtol=1e-10,atol=1e-12)


def test_robust_candidate_status_requires_global_fdr():
    frame=pd.DataFrame({
        "status":["statistically_interesting"]*2,
        "raw_p":[1e-6,1e-6],
        "screen_bh_fdr_p_global":[.2,.01],
        "year_consistency":[1.,1.],
        "symbol_breadth":[.8,.8],
    })
    classified=_classify_detailed_candidates(frame)
    assert classified.status.tolist()==["interesting_not_global","robust_anomaly_candidate"]
