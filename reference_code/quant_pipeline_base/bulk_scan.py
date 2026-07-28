from __future__ import annotations

from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from .config import ScanConfig
from .registry import FeatureSpec
from .scanner import benjamini_hochberg


def cuda_screen(
    feature_frame: pd.DataFrame,
    target_frame: pd.DataFrame,
    features: list[FeatureSpec],
    targets: list[str],
    config: ScanConfig,
    prior: pd.DataFrame,
    journal: Path,
) -> pd.DataFrame:
    """Vectorized all-pair screen; exact diagnostics are reserved for survivors."""
    completed={(r.feature,r.target) for r in prior.itertuples()} if not prior.empty else set()
    active_features=[s for s in features if any((s.name,t) not in completed for t in targets)]
    if not active_features: return prior
    import torch
    device=torch.device(config.cuda_device if config.use_cuda and torch.cuda.is_available() else "cpu")
    feature_names=[s.name for s in active_features]
    x_np=feature_frame[feature_names].to_numpy(dtype=np.float32,copy=True); y_np=target_frame[targets].to_numpy(dtype=np.float32,copy=True)
    tx=torch.as_tensor(x_np,device=device); ty=torch.as_tensor(y_np,device=device)
    corr,n,pair_mean_x,pair_mean_y,vx,vy,cov=_pair_moments_stable(tx,ty)
    cluster_codes=torch.as_tensor(pd.factorize(feature_frame.session_date,sort=False)[0],device=device,dtype=torch.long)
    cluster_se,cluster_t=_clustered_inference_from_moments(
        tx,ty,cluster_codes,n,pair_mean_x,pair_mean_y,vx,cov
    )
    # Approximate ranks on a deterministic 200k-row sample. Exact Spearman is
    # recomputed for promoted pairs in the diagnostic pass.
    sample=np.linspace(0,len(feature_frame)-1,min(len(feature_frame),200_000),dtype=np.int64)
    rx=_percentile_bins(tx,x_np[sample],100); ry=_percentile_bins(ty,y_np[sample],100)
    rank_corr,*_=_pair_moments_stable(rx,ry)
    sessions=max(3,feature_frame.session_date.nunique()); symbols=feature_frame.symbol.nunique(); rows=[]
    corr=corr.cpu().numpy(); rank_corr=rank_corr.cpu().numpy(); n=n.cpu().numpy(); pair_mean_y=pair_mean_y.cpu().numpy(); vx=vx.cpu().numpy(); vy=vy.cpu().numpy(); cov=cov.cpu().numpy(); cluster_se=cluster_se.cpu().numpy(); cluster_t=cluster_t.cpu().numpy()
    # Aggregate all ten deciles for every target in one scatter operation per
    # feature. This replaces thousands of full-column masks and device syncs.
    decile_means=[]; decile_counts=[]
    finite_y=torch.isfinite(ty)
    for i in range(len(active_features)):
        feature_valid=torch.isfinite(tx[:,i]); bins=(rx[:,i].nan_to_num(0).clamp(0,99)//10).long()
        feature_means=np.full((10,ty.shape[1]),np.nan,dtype=np.float32); feature_counts=np.zeros((10,ty.shape[1]),dtype=np.int64)
        for j in range(ty.shape[1]):
            valid=feature_valid&finite_y[:,j]; valid_bins=bins[valid]
            counts=torch.bincount(valid_bins,minlength=10)
            sums=torch.zeros(10,device=device,dtype=ty.dtype); sums.scatter_add_(0,valid_bins,ty[valid,j])
            counts_np=counts.cpu().numpy(); feature_counts[:,j]=counts_np
            feature_means[:,j]=np.where(counts_np>0,(sums/counts.clamp_min(1)).cpu().numpy(),np.nan)
        decile_means.append(feature_means); decile_counts.append(feature_counts)
    for i,spec in enumerate(active_features):
        for j,target in enumerate(targets):
            if (spec.name,target) in completed: continue
            count=int(n[i,j])
            base={"feature":spec.name,"feature_family":spec.family,"feature_classification":spec.classification,"target":target,"target_family":target.removesuffix("_benchmark_adjusted"),"n":count,"sessions":sessions,"symbols":symbols}
            pair_variance=float(vx[i,j]/max(count,1))
            if not np.isfinite(pair_variance) or pair_variance<1e-14:
                rows.append({**base,"status":"constant_feature"}); continue
            if count<config.min_observations:
                rows.append({**base,"status":"insufficient_data"}); continue
            counts=decile_counts[i][:,j].tolist(); means=decile_means[i][:,j].astype(float)
            means=np.where(np.asarray(counts)>0,means,np.nan)
            finite=np.isfinite(means)
            monotonicity=float(stats.spearmanr(np.arange(10)[finite],np.asarray(means)[finite]).statistic) if finite.sum()>=3 else np.nan
            spread=float(means[-1]-means[0]) if np.isfinite(means[-1]) and np.isfinite(means[0]) else np.nan
            r=float(corr[i,j]); t=float(cluster_t[i,j]); p=float(2*stats.norm.sf(abs(t))) if np.isfinite(t) else np.nan
            variance_x=vx[i,j]; slope=float(cov[i,j]/variance_x) if variance_x>0 else np.nan
            mean_y=float(pair_mean_y[i,j]); var_y=max(0,float(vy[i,j]/count))
            shape="positive_monotonic" if monotonicity>=.7 else "negative_monotonic" if monotonicity<=-.7 else "no_stable_shape"
            rows.append({**base,"mean_target":mean_y,"std_target":sqrt(var_y),"pearson":r,"spearman":float(rank_corr[i,j]),"slope":slope,"cluster_se":float(cluster_se[i,j]),"cluster_t":t,"raw_p":p,"top_bottom_spread":spread,"monotonicity":monotonicity,"shape":shape,"status":"cuda_screened"})
    additions=pd.DataFrame(rows)
    if not additions.empty:
        additions.to_csv(journal,mode="a",header=not journal.exists(),index=False)
    out=pd.concat([prior,additions],ignore_index=True)
    del tx,ty,rx,ry,cluster_codes
    if device.type=="cuda": torch.cuda.empty_cache()
    return out


def finalize_screen(result: pd.DataFrame) -> pd.DataFrame:
    return _finalize(result)


def _pair_moments(x,y):
    import torch
    mx=torch.isfinite(x).float(); my=torch.isfinite(y).float(); x0=torch.nan_to_num(x); y0=torch.nan_to_num(y)
    n=mx.T@my; sx=x0.T@my; sy=mx.T@y0; sxx=(x0*x0).T@my; syy=mx.T@(y0*y0); sxy=x0.T@y0
    cov=sxy-sx*sy/n.clamp_min(1); vx=sxx-sx*sx/n.clamp_min(1); vy=syy-sy*sy/n.clamp_min(1); corr=cov/torch.sqrt((vx*vy).clamp_min(1e-20))
    return corr,n,sx,sy,sxx,syy,sxy


def _pair_moments_stable(x,y,feature_block: int = 8):
    """Two-pass pairwise moments, stable when target availability changes feature variance."""
    import torch
    features=x.shape[1]; targets=y.shape[1]; shape=(features,targets)
    corr=torch.full(shape,float("nan"),device=x.device,dtype=x.dtype); n=torch.zeros(shape,device=x.device,dtype=x.dtype)
    mean_x=torch.full(shape,float("nan"),device=x.device,dtype=x.dtype); mean_y=torch.full(shape,float("nan"),device=x.device,dtype=x.dtype); vx=torch.zeros(shape,device=x.device,dtype=x.dtype); vy=torch.zeros(shape,device=x.device,dtype=x.dtype); cov=torch.zeros(shape,device=x.device,dtype=x.dtype)
    for j in range(targets):
        ycol=y[:,j]
        for start in range(0,features,feature_block):
            stop=min(features,start+feature_block); xb=x[:,start:stop]
            valid=torch.isfinite(xb)&torch.isfinite(ycol[:,None]); counts=valid.sum(0).to(x.dtype).clamp_min(1)
            mx=torch.where(valid,xb,0).sum(0)/counts; my=torch.where(valid,ycol[:,None],0).sum(0)/counts
            dx=torch.where(valid,xb-mx,0); dy=torch.where(valid,ycol[:,None]-my,0)
            block_vx=dx.square().sum(0); block_vy=dy.square().sum(0); block_cov=(dx*dy).sum(0)
            n[start:stop,j]=counts; mean_x[start:stop,j]=mx; mean_y[start:stop,j]=my; vx[start:stop,j]=block_vx; vy[start:stop,j]=block_vy; cov[start:stop,j]=block_cov
            corr[start:stop,j]=(block_cov/torch.sqrt((block_vx*block_vy).clamp_min(1e-20))).clamp(-1,1)
    return corr,n,mean_x,mean_y,vx,vy,cov


def _clustered_inference_from_moments(x,y,clusters,n,mean_x,mean_y,vx,cov,feature_block: int = 8):
    """One-way session-clustered OLS slope errors for every feature-target pair.

    The previous CUDA screen substituted the number of sessions into an IID
    correlation t statistic. That is not a clustered standard error and can be
    dramatically more conservative than the exact sandwich calculation.  For
    simple OLS with an intercept, each cluster's slope influence is
    ``sum((x-xbar) * residual)``.  Accumulating those scores gives the exact
    one-way cluster sandwich variance without materializing a regression per
    pair.
    """
    import torch

    shape=(x.shape[1],y.shape[1])
    standard_error=torch.full(shape,float("nan"),device=x.device,dtype=x.dtype)
    t_stat=torch.full(shape,float("nan"),device=x.device,dtype=x.dtype)
    cluster_count=int(clusters.max().item())+1 if clusters.numel() else 0
    for j in range(y.shape[1]):
        ycol=y[:,j]
        for start in range(0,x.shape[1],feature_block):
            stop=min(x.shape[1],start+feature_block); xb=x[:,start:stop]
            valid=torch.isfinite(xb)&torch.isfinite(ycol[:,None])
            mx=mean_x[start:stop,j]; my=mean_y[start:stop,j]
            block_vx=vx[start:stop,j]; slope=cov[start:stop,j]/block_vx.clamp_min(1e-20)
            dx=torch.where(valid,xb-mx,0); dy=torch.where(valid,ycol[:,None]-my,0)
            influence=dx*(dy-dx*slope)
            cluster_scores=torch.zeros((cluster_count,stop-start),device=x.device,dtype=x.dtype)
            cluster_scores.index_add_(0,clusters,influence)
            se=torch.sqrt(cluster_scores.square().sum(0).clamp_min(0))/block_vx.clamp_min(1e-20)
            valid_result=(n[start:stop,j]>2)&torch.isfinite(se)&se.gt(0)&block_vx.gt(1e-14)
            standard_error[start:stop,j]=torch.where(valid_result,se,torch.nan)
            t_stat[start:stop,j]=torch.where(valid_result,slope/se,torch.nan)
    return standard_error,t_stat


def _percentile_bins(tensor, sample: np.ndarray, bins: int):
    import torch
    out=torch.full_like(tensor,float("nan")); quantiles=np.linspace(0,1,bins+1)[1:-1]
    for column in range(tensor.shape[1]):
        values=sample[:,column]; values=values[np.isfinite(values)]
        if not len(values): continue
        edges=torch.as_tensor(np.unique(np.quantile(values,quantiles)),device=tensor.device,dtype=tensor.dtype)
        valid=torch.isfinite(tensor[:,column]); out[valid,column]=torch.bucketize(tensor[valid,column],edges).float()
    return out


def _finalize(result: pd.DataFrame) -> pd.DataFrame:
    if result.empty:return result
    result["bh_fdr_p_group"]=result.groupby(["feature_family","target_family"],dropna=False).raw_p.transform(benjamini_hochberg)
    result["bh_fdr_p_global"]=benjamini_hochberg(result.raw_p)
    # Global FDR is the promotion gate; grouped FDR remains diagnostic only.
    result["bh_fdr_p"]=result["bh_fdr_p_global"]
    eligible=result.raw_p.notna()&~result.status.isin(["constant_feature","insufficient_data"])
    result.loc[eligible,"status"]="no_meaningful_relationship"
    result.loc[eligible&(result.bh_fdr_p_global<.05),"status"]="statistically_interesting"
    result["test_count"]=int(result.raw_p.notna().sum()); effect=result.top_bottom_spread.abs().fillna(0).rank(pct=True); significance=(1-result.bh_fdr_p.fillna(1)).clip(0,1)
    result["anomaly_score"]=.4*effect+.35*significance+.25*result.monotonicity.abs().fillna(0)
    result["redundancy_group"]=result.feature.str.replace(r"_(1|2|3|4|5|6|8|10|12|15|20|24|30|36|48|60|78)$","_LOOKBACK",regex=True)
    return result.sort_values("anomaly_score",ascending=False,na_position="last").reset_index(drop=True)
