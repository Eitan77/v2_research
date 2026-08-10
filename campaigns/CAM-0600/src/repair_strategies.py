from __future__ import annotations

import numpy as np
import pandas as pd

from adaptation_strategies import positive_sleeve
from baseline_strategies import Variant, alpha_combo_weights, multiple_cluster_weights, pivot_weights, weighted_regression_weights
from deep_strategies import concentrate_positive, liquid_mask, trend_mask
from fundamentals import FundamentalMatrices
from suite_core import Panel, forward_fill_signal_weights, trailing_return, weekly_indices, month_end_indices


def sample_and_hold(weights: np.ndarray, signals: np.ndarray) -> np.ndarray:
    raw=np.zeros_like(weights); raw[signals]=weights[signals]
    return forward_fill_signal_weights(raw,signals)


def pair_long_cheap(panel: Panel, a: str, b: str, lookback: int, z_window: int, threshold: float) -> np.ndarray:
    ca,cb=panel.symbol_to_col[a],panel.symbol_to_col[b]
    ra=trailing_return(panel,lookback,0)[:,ca]
    rb=trailing_return(panel,lookback,0)[:,cb]
    spread=ra-rb
    mean=pd.Series(spread).rolling(z_window,min_periods=max(20,z_window//2)).mean().to_numpy(float)
    std=pd.Series(spread).rolling(z_window,min_periods=max(20,z_window//2)).std(ddof=1).to_numpy(float)
    z=np.where(std>0,(spread-mean)/std,np.nan)
    out=np.zeros_like(panel.adj_close)
    out[z>=threshold,cb]=1.0
    out[z<=-threshold,ca]=1.0
    return out

def ibs_multi_hold(panel: Panel, threshold: float, top_k: int, hold_days: int, trend: bool) -> np.ndarray:
    spread=panel.adj_high-panel.adj_low
    ibs=np.where(spread>0,(panel.adj_close-panel.adj_low)/spread,np.nan)
    base_mask=liquid_mask(panel,.60)
    if trend: base_mask &= trend_mask(panel,200)
    entries=np.zeros_like(panel.adj_close)
    for i in range(panel.n_dates):
        cols=np.flatnonzero(base_mask[i]&np.isfinite(ibs[i])&(ibs[i]<=threshold))
        if not len(cols): continue
        chosen=cols[np.argsort(ibs[i,cols],kind="stable")[:min(top_k,len(cols))]]
        entries[i,chosen]=1.0/len(chosen)
    out=np.zeros_like(entries)
    for i in range(panel.n_dates):
        active=np.zeros(panel.n_symbols)
        for j in range(max(0,i-hold_days+1),i+1): active += entries[j]/hold_days
        gross=active.sum()
        if gross>1: active/=gross
        out[i]=active
    return out


def build_repair_variants(campaign_id: str, panels: dict[str,Panel], f: dict[str,FundamentalMatrices]) -> list[Variant]:
    out=[]
    if campaign_id=="CAM-0606":
        p=panels["etf"]
        pairs=(("QQQ","SPY"),("SMH","XLK"),("IWM","SPY"),("XLY","XLP"),("XLE","USO"),("TLT","SHY"))
        for a,b in pairs:
            for lookback in (1,2,5):
                for threshold in (1.0,1.5,2.0):
                    weights=pair_long_cheap(p,a,b,lookback,63,threshold)
                    out.append(Variant(campaign_id,f"pair_{a}_{b}__r{lookback}__z{threshold:g}__longcheap",p,weights,"open_to_close",1,{"pair":[a,b],"lookback":lookback,"z_window":63,"threshold":threshold,"mode":"long_cheap_intraday","pair_selection":"economic_prior"}))
    elif campaign_id in {"CAM-0608","CAM-0609"}:
        for name in ("sp500","qqq"):
            p=panels[name]
            for lookback in (5,10,20):
                if campaign_id=="CAM-0608": raw,report=multiple_cluster_weights(p,panels["etf"],126,lookback)
                else: raw,report=weighted_regression_weights(p,f[name],lookback,126)
                for top_k in (3,10):
                    concentrated=concentrate_positive(raw,top_k,liquid_mask(p,.50))
                    for cadence,signals in (("weekly",weekly_indices(p.dates)),("monthly",month_end_indices(p.dates))):
                        weights=sample_and_hold(concentrated,signals)
                        out.append(Variant(campaign_id,f"{name}__slow_residual_r{lookback}__top{top_k}__{cadence}",p,weights,"open_to_next_open",1,{"lookback":lookback,"top_k":top_k,"cadence":cadence,"mode":"long_negative_residual",**report}))
    elif campaign_id=="CAM-0613":
        for name in ("sp500","qqq","etf"):
            p=panels[name]; raw,realized,report=pivot_weights(p,"long")
            for top_k in ((3,10,20) if name!="etf" else (1,3,5)):
                for trend in (False,True):
                    mask=liquid_mask(p,.35)
                    if trend: mask &= trend_mask(p,100)
                    weights=concentrate_positive(raw,top_k,mask)
                    out.append(Variant(campaign_id,f"{name}__pivot_top{top_k}__trend{int(trend)}",p,weights,"return_override",0,{"top_k":top_k,"trend":trend,"liquidity":"top35pct",**report},realized))
    elif campaign_id=="CAM-0617":
        for name in ("qqq","etf"):
            p=panels[name]
            for history,forecast in ((20,5),(60,5),(60,20),(120,20)):
                raw,report=alpha_combo_weights(p,history,forecast)
                for top_k in ((3,10) if name=="qqq" else (1,3,5)):
                    base=concentrate_positive(raw,top_k,liquid_mask(p,.50))
                    for cadence,signals in (("weekly",weekly_indices(p.dates)),("monthly",month_end_indices(p.dates))):
                        for gate in (False,True):
                            weights=sample_and_hold(base,signals)
                            if gate: weights*=trend_mask(p,200)
                            out.append(Variant(campaign_id,f"{name}__alpha_M{history}_E{forecast}__top{top_k}__{cadence}__trend{int(gate)}",p,weights,"open_to_next_open",1,{"history":history,"forecast":forecast,"top_k":top_k,"cadence":cadence,"trend":gate,**report}))
    elif campaign_id=="CAM-0621":
        p=panels["etf"]
        for threshold in (.10,.20,.30):
            for top_k in (1,3,5):
                for hold in (2,3,5):
                    for trend in (False,True):
                        weights=ibs_multi_hold(p,threshold,top_k,hold,trend)
                        out.append(Variant(campaign_id,f"etf__ibs{int(threshold*100)}__top{top_k}__hold{hold}__trend{int(trend)}",p,weights,"open_to_next_open",1,{"threshold":threshold,"top_k":top_k,"hold_days":hold,"trend":trend}))
    if not out: raise RuntimeError(campaign_id)
    return out
