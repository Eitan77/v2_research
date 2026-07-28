from __future__ import annotations

from dataclasses import asdict
from math import erf, sqrt

import numpy as np
import pandas as pd
from scipy import stats

from .config import ScanConfig
from .registry import FeatureSpec
from .gpu import CorrelationBackend


def _normal_pvalue(z: float) -> float:
    return float(1 - erf(abs(z) / sqrt(2)))


def _clustered_slope(x: np.ndarray, y: np.ndarray, clusters: np.ndarray) -> tuple[float, float, float, float]:
    """OLS slope with one-way session-cluster sandwich error, no IID claim."""
    X=np.column_stack((np.ones(len(x)),x)); beta=np.linalg.pinv(X.T@X)@(X.T@y); resid=y-X@beta
    bread=np.linalg.pinv(X.T@X)
    codes,_=pd.factorize(clusters,sort=False)
    score0=np.bincount(codes,weights=resid); score1=np.bincount(codes,weights=x*resid)
    meat=np.array([[score0@score0,score0@score1],[score0@score1,score1@score1]])
    v=bread@meat@bread; se=float(np.sqrt(max(v[1,1],0))); t=float(beta[1]/se) if se else np.nan
    return float(beta[1]),se,t,_normal_pvalue(t) if np.isfinite(t) else np.nan


def _quantiles(x: pd.Series, y: pd.Series, q: int, min_bin: int) -> tuple[dict, pd.DataFrame]:
    try: bins=pd.qcut(x.rank(method="first"),q,labels=False,duplicates="drop")
    except ValueError: return {"top_bottom_spread":np.nan,"monotonicity":np.nan,"shape":"insufficient"},pd.DataFrame()
    tab=pd.DataFrame({"bin":bins,"target":y}).groupby("bin",observed=True).target.agg(["count","mean","median",lambda z:(z>0).mean(),"std"]).rename(columns={"<lambda_0>":"win_rate"}).reset_index()
    tab["se"]=tab["std"]/np.sqrt(tab["count"]); tab["ci_low"]=tab["mean"]-1.96*tab.se; tab["ci_high"]=tab["mean"]+1.96*tab.se
    if len(tab)<3 or tab["count"].min()<min_bin: return {"top_bottom_spread":np.nan,"monotonicity":np.nan,"shape":"insufficient"},tab
    rho=float(stats.spearmanr(tab.bin,tab["mean"]).statistic); spread=float(tab["mean"].iloc[-1]-tab["mean"].iloc[0]); diffs=np.diff(tab["mean"])
    if abs(rho)>=.7: shape="positive_monotonic" if rho>0 else "negative_monotonic"
    elif max(tab["mean"].iloc[0],tab["mean"].iloc[-1])-tab["mean"].iloc[len(tab)//2]>.5*abs(spread): shape="two_sided_tail"
    elif tab["mean"].iloc[len(tab)//2] > max(tab["mean"].iloc[0],tab["mean"].iloc[-1]): shape="inverted_u"
    elif tab["mean"].iloc[len(tab)//2] < min(tab["mean"].iloc[0],tab["mean"].iloc[-1]): shape="u_shaped"
    elif abs(diffs[-1])>2*np.nanmedian(abs(diffs[:-1])): shape="positive_tail" if diffs[-1]>0 else "negative_tail"
    else: shape="no_stable_shape"
    return {"top_bottom_spread":spread,"monotonicity":rho,"shape":shape},tab


def _cross_sectional_ic(frame: pd.DataFrame, feature: str, target: str) -> dict:
    groups=frame["decision_ts"]
    xr=frame[feature].groupby(groups,sort=False).rank(method="average")
    yr=frame[target].groupby(groups,sort=False).rank(method="average")
    work=pd.DataFrame({"group":groups,"x":xr,"y":yr}); work["xx"]=xr*xr; work["yy"]=yr*yr; work["xy"]=xr*yr
    agg=work.groupby("group",sort=False).agg(n=("x","size"),sx=("x","sum"),sy=("y","sum"),sxx=("xx","sum"),syy=("yy","sum"),sxy=("xy","sum"))
    cov=agg.sxy-agg.sx*agg.sy/agg.n; vx=agg.sxx-agg.sx*agg.sx/agg.n; vy=agg.syy-agg.sy*agg.sy/agg.n
    ic=(cov/np.sqrt(vx*vy)).replace([np.inf,-np.inf],np.nan).dropna()
    if len(ic)<2:return {"ic_mean":np.nan,"ic_median":np.nan,"ic_std":np.nan,"ic_t":np.nan,"ic_positive_pct":np.nan}
    return {"ic_mean":float(ic.mean()),"ic_median":float(ic.median()),"ic_std":float(ic.std(ddof=1)),"ic_t":float(ic.mean()/(ic.std(ddof=1)/sqrt(len(ic)))),"ic_positive_pct":float((ic>0).mean())}


def benjamini_hochberg(values: pd.Series) -> pd.Series:
    valid=values.notna(); p=values[valid].to_numpy(); order=np.argsort(p); ranked=p[order]; adj=np.minimum.accumulate((ranked*len(p)/np.arange(1,len(p)+1))[::-1])[::-1]; out=pd.Series(np.nan,index=values.index); out.loc[values[valid].iloc[order].index]=np.minimum(adj,1); return out


def scan(frame: pd.DataFrame, features: list[FeatureSpec], targets: list[str], config: ScanConfig, checkpoint_path=None, *, skip_dense: bool=False, direction_hint: float | None=None) -> tuple[pd.DataFrame, dict[tuple[str,str],pd.DataFrame]]:
    rows=[]; quantile_tables={}; backend=CorrelationBackend(config.use_cuda,config.cuda_device)
    completed=set()
    if checkpoint_path is not None and config.resume and pd.io.common.file_exists(checkpoint_path):
        prior=pd.read_csv(checkpoint_path); rows=prior.to_dict("records"); completed={(r["feature"],r["target"]) for r in rows}
    for spec in features:
        for target in targets:
            if (spec.name,target) in completed: continue
            sub=frame[["session_date","symbol","decision_ts",spec.name,target]].replace([np.inf,-np.inf],np.nan).dropna()
            base={"feature":spec.name,"feature_family":spec.family,"feature_classification":spec.classification,"target":target,"n":len(sub),"sessions":sub.session_date.nunique(),"symbols":sub.symbol.nunique()}
            if len(sub)<config.min_observations or base["sessions"]<config.min_sessions or base["symbols"]<config.min_symbols:
                rows.append({**base,"status":"insufficient_data"});continue
            x=sub[spec.name].astype(float);y=sub[target].astype(float)
            slope,se,t,p=_clustered_slope(x.to_numpy(),y.to_numpy(),sub.session_date.astype(str).to_numpy())
            if skip_dense:
                qstats={"top_bottom_spread":np.nan,"monotonicity":np.nan,"shape":"pending_gpu"}; qtab=pd.DataFrame(); pearson=spearman=np.nan
            else:
                qstats,qtab=_quantiles(x,y,config.quantiles,config.min_bin_observations); pearson,spearman=backend.correlations(x.to_numpy(),y.to_numpy())
            annual=sub.assign(year=pd.to_datetime(sub.session_date).dt.year).groupby("year").apply(lambda z:z[spec.name].corr(z[target],method="spearman"),include_groups=False).dropna()
            symbol_corr=sub.groupby("symbol").apply(lambda z:z[spec.name].corr(z[target],method="spearman"),include_groups=False).dropna()
            if skip_dense: outlier_sensitivity=np.nan
            else:
                clipped=x.clip(x.quantile(.01),x.quantile(.99)); clip_corr=float(clipped.corr(y,method="spearman")); outlier_sensitivity=abs(spearman-clip_corr)
            cs=_cross_sectional_ic(sub,spec.name,target) if spec.classification=="cross_sectional" else {}
            status="statistically_interesting" if p<.05 else "no_meaningful_relationship"
            local=pd.to_datetime(sub.decision_ts,utc=True).dt.tz_convert("America/New_York"); sub=sub.assign(time_bucket=(local.dt.hour*60+local.dt.minute).floordiv(60),year=pd.to_datetime(sub.session_date).dt.year)
            time_corr=sub.groupby("time_bucket").apply(lambda z:z[spec.name].corr(z[target],method="spearman"),include_groups=False).dropna()
            direction=spearman if np.isfinite(spearman) else direction_hint
            rows.append({**base,"mean_target":float(y.mean()),"median_target":float(y.median()),"std_target":float(y.std()),"win_rate":float((y>0).mean()),"skewness":float(stats.skew(y,nan_policy="omit")),"downside_p05":float(y.quantile(.05)),"upside_p95":float(y.quantile(.95)),"pearson":pearson,"spearman":spearman,"slope":slope,"cluster_se":se,"cluster_t":t,"raw_p":p,"ci_low":slope-1.96*se,"ci_high":slope+1.96*se,"year_consistency":float((annual*np.sign(direction)>0).mean()) if len(annual) and direction is not None else np.nan,"symbol_breadth":float((symbol_corr*np.sign(direction)>0).mean()) if len(symbol_corr) and direction is not None else np.nan,"time_stability":float((time_corr*np.sign(direction)>0).mean()) if len(time_corr) and direction is not None else np.nan,"outlier_sensitivity":outlier_sensitivity,"status":status,**qstats,**cs})
            quantile_tables[(spec.name,target)]=qtab
            if checkpoint_path is not None and len(rows)%config.checkpoint_every_pairs==0: pd.DataFrame(rows).to_csv(checkpoint_path,index=False)
    result=pd.DataFrame(rows)
    if not result.empty:
        result["target_family"]=result.target.str.replace(r"_benchmark_adjusted$","",regex=True)
        result["bh_fdr_p"]=result.groupby(["feature_family","target_family"],dropna=False).raw_p.transform(benjamini_hochberg)
        result["test_count"]=int(result.raw_p.notna().sum())
        result["redundancy_group"]=result.feature.str.replace(r"_(1|2|3|5|10|15|30|60|1560|4680)$","_LOOKBACK",regex=True)
        effect=result.top_bottom_spread.abs().fillna(0).rank(pct=True); significance=(1-result.bh_fdr_p.fillna(1)).clip(0,1); stability=result[["year_consistency","symbol_breadth"]].mean(axis=1).fillna(0)
        result["anomaly_score"]=.35*effect+.35*significance+.2*result.monotonicity.abs().fillna(0)+.1*stability-.2*result.outlier_sensitivity.fillna(0)
        result.loc[(result.bh_fdr_p<.05)&(result.year_consistency>=.6)&(result.symbol_breadth>=.6),"status"]="robust_anomaly_candidate"
        result=result.sort_values("anomaly_score",ascending=False,na_position="last")
    if checkpoint_path is not None: result.to_csv(checkpoint_path,index=False)
    return result,quantile_tables
