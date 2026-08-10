from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from adaptation_strategies import (
    cash_vol_target,
    positive_sleeve,
    shrinkage_optimizer_weights,
)
from baseline_strategies import (
    Variant,
    alpha_combo_weights,
    build_sue_scores,
    channel_weights,
    eligible,
    moving_average,
    multiple_cluster_weights,
    pivot_weights,
    rank_percentile,
    residual_momentum_score,
    sector_panel_mask,
    weighted_regression_weights,
)
from fundamentals import FundamentalMatrices
from suite_core import (
    SECTOR_ETFS,
    Panel,
    forward_fill_signal_weights,
    month_end_indices,
    rank_weights,
    trailing_return,
    trailing_vol,
    weekly_indices,
)


def liquid_mask(panel: Panel, fraction: float = 0.50, window: int = 63) -> np.ndarray:
    dollar = pd.DataFrame(panel.raw_close * panel.volume).rolling(window, min_periods=max(20, window // 2)).median().to_numpy(float)
    out = np.zeros_like(panel.member, dtype=bool)
    base = eligible(panel) & np.isfinite(dollar)
    for i in range(panel.n_dates):
        cols = np.flatnonzero(base[i])
        if not len(cols):
            continue
        n = max(1, int(np.ceil(len(cols) * fraction)))
        chosen = cols[np.argsort(dollar[i, cols], kind="stable")[-n:]]
        out[i, chosen] = True
    return out


def trend_mask(panel: Panel, window: int = 200) -> np.ndarray:
    ma = moving_average(panel, window)
    return eligible(panel) & np.isfinite(ma) & (panel.adj_close > ma)


def benchmark_gate(panel: Panel, panic_defense: bool) -> np.ndarray:
    if not panic_defense:
        return np.ones(panel.n_dates, dtype=bool)
    symbol = "SPY" if "SPY" in panel.symbol_to_col else "QQQ"
    col = panel.symbol_to_col[symbol]
    ma = moving_average(panel, 200)
    vol = trailing_vol(panel, 63)
    vol_series = pd.Series(vol[:, col]).rolling(252, min_periods=126).median().to_numpy(float)
    return (panel.adj_close[:, col] > ma[:, col]) | (vol[:, col] <= vol_series)


def concentrate_positive(weights: np.ndarray, top_k: int, mask: np.ndarray | None = None) -> np.ndarray:
    out = np.zeros_like(weights)
    for i in range(len(out)):
        row = np.clip(weights[i], 0.0, None)
        if mask is not None:
            row = np.where(mask[i], row, 0.0)
        cols = np.flatnonzero(row > 0)
        if not len(cols):
            continue
        chosen = cols[np.argsort(row[cols], kind="stable")[-min(top_k, len(cols)):]]
        out[i, chosen] = row[chosen] / row[chosen].sum()
    return out


def rank_long(
    panel: Panel,
    score: np.ndarray,
    signals: np.ndarray,
    mask: np.ndarray,
    top_k: int,
    inverse_vol_window: int | None = None,
) -> np.ndarray:
    inv = trailing_vol(panel, inverse_vol_window) if inverse_vol_window else None
    return rank_weights(score, mask & np.isfinite(score), signals, mode="long", top_k=top_k, inverse_vol=inv)


def monthly_overlap(weights: np.ndarray, signals: np.ndarray, hold_months: int) -> np.ndarray:
    signal_only = np.zeros_like(weights)
    signal_only[signals] = weights[signals]
    out = np.zeros_like(weights)
    for k, i in enumerate(signals):
        starts = signals[max(0, k - hold_months + 1):k + 1]
        row = signal_only[starts].sum(axis=0) / float(hold_months)
        gross = row.sum()
        if gross > 1:
            row /= gross
        out[i] = row
    return forward_fill_signal_weights(out, signals)


def cross_sectional_reversal(
    panel: Panel,
    lookback: int,
    top_k: int,
    zmin: float,
    mask: np.ndarray,
) -> np.ndarray:
    score = trailing_return(panel, lookback, 0)
    out = np.zeros_like(score)
    for i in range(lookback, panel.n_dates):
        cols = np.flatnonzero(mask[i] & np.isfinite(score[i]))
        if len(cols) < 3:
            continue
        values = score[i, cols]
        sd = float(np.std(values, ddof=1))
        if not np.isfinite(sd) or sd <= 0:
            continue
        z = (values - np.mean(values)) / sd
        candidates = cols[z <= -zmin]
        if not len(candidates):
            continue
        chosen = candidates[np.argsort(score[i, candidates], kind="stable")[:min(top_k, len(candidates))]]
        out[i, chosen] = 1.0 / len(chosen)
    return out


def active_trend_rank(
    panel: Panel,
    condition: np.ndarray,
    signals: np.ndarray,
    top_k: int,
    score_kind: str,
) -> np.ndarray:
    if score_kind == "momentum":
        score = trailing_return(panel, 126, 21)
    elif score_kind == "risk_adjusted":
        score = trailing_return(panel, 126, 21) / trailing_vol(panel, 63)
    else:
        raise ValueError(score_kind)
    mask = eligible(panel) & condition & liquid_mask(panel, 0.50)
    return rank_long(panel, score, signals, mask, top_k, 63 if score_kind == "risk_adjusted" else None)


def threshold_vol_target(
    panel: Panel,
    risky: str,
    target: float,
    window: int,
    cadence: str,
    threshold: float,
    trend_defense: bool,
) -> np.ndarray:
    signals = weekly_indices(panel.dates) if cadence == "weekly" else month_end_indices(panel.dates)
    vol = trailing_vol(panel, window)
    risky_col, cash_col = panel.symbol_to_col[risky], panel.symbol_to_col["BIL"]
    ma = moving_average(panel, 200)
    raw = np.zeros_like(panel.adj_close)
    last = np.array([0.0, 1.0])
    for i in signals:
        sigma = vol[i, risky_col]
        if not np.isfinite(sigma) or sigma <= 0:
            continue
        risky_weight = min(1.0, target / sigma)
        if trend_defense and panel.adj_close[i, risky_col] < ma[i, risky_col]:
            risky_weight *= 0.5
        desired = np.array([risky_weight, 1.0 - risky_weight])
        relative = abs(desired[0] - last[0]) / max(last[0], 0.05)
        if relative >= threshold or not raw[:i + 1].any():
            last = desired
        raw[i, risky_col], raw[i, cash_col] = last
    return forward_fill_signal_weights(raw, signals)


def build_deep_variants(campaign_id: str, panels: dict[str, Panel], f: dict[str, FundamentalMatrices]) -> list[Variant]:
    out: list[Variant] = []

    if campaign_id == "CAM-0600":
        for name in ("sp500", "qqq", "etf"):
            p = panels[name]
            signals = month_end_indices(p.dates)
            for formation, skip in ((63, 0), (126, 0), (126, 21), (252, 21)):
                score = trailing_return(p, formation, skip)
                for top_k in ((3, 10) if name != "etf" else (1, 3)):
                    for gate in ("liquid", "liquid_trend"):
                        mask = liquid_mask(p, .50)
                        if gate == "liquid_trend":
                            mask &= trend_mask(p, 200)
                        mask &= score > 0
                        weights = rank_long(p, score, signals, mask, top_k, 63)
                        for panic in (False, True):
                            candidate = weights * benchmark_gate(p, panic)[:, None]
                            out.append(Variant(campaign_id, f"{name}__mom{formation}_skip{skip}__top{top_k}__{gate}__panic{int(panic)}", p, candidate, "open_to_next_open", 1, {"formation":formation,"skip":skip,"top_k":top_k,"gate":gate,"panic_defense":panic,"weighting":"inverse_vol63"}))

    elif campaign_id == "CAM-0601":
        for name in ("sp500", "qqq"):
            p = panels[name]
            score, report = build_sue_scores(p, include_sec=True)
            signals = month_end_indices(p.dates)
            mom20 = trailing_return(p, 20, 0)
            for hold in (1, 3, 6):
                for top_k in (5, 10):
                    for confirm in ("none", "price20"):
                        mask = liquid_mask(p, .50) & np.isfinite(score)
                        if confirm == "price20": mask &= mom20 > 0
                        base = rank_long(p, score, signals, mask, top_k, 63)
                        weights = monthly_overlap(base, signals, hold)
                        out.append(Variant(campaign_id, f"{name}__sue__hold{hold}__top{top_k}__{confirm}", p, weights, "open_to_next_open", 1, {"hold_months":hold,"top_k":top_k,"confirmation":confirm,"liquidity":"top_half","source_repair":report}))

    elif campaign_id in {"CAM-0602", "CAM-0603", "CAM-0604"}:
        for name in ("sp500", "qqq"):
            p, signals = panels[name], month_end_indices(panels[name].dates)
            mask = liquid_mask(p, .50)
            mom = rank_percentile(trailing_return(p, 252, 21), mask, signals)
            val = rank_percentile(f[name].book_to_price, mask, signals)
            quality = rank_percentile(f[name].profitability - f[name].leverage, mask, signals)
            lowvol = 1.0 - rank_percentile(trailing_vol(p, 126), mask, signals)
            if campaign_id == "CAM-0602":
                specifications = (("value", val), ("value_quality", .65*val+.35*quality), ("value_momentum", .65*val+.35*mom))
            elif campaign_id == "CAM-0603":
                specifications = (("lowvol", lowvol), ("lowvol_trend", .70*lowvol+.30*mom), ("lowvol_quality", .70*lowvol+.30*quality))
            else:
                specifications = (("equal4", (mom+val+quality+lowvol)/4), ("mom_quality", .50*mom+.30*quality+.20*lowvol), ("value_quality", .45*val+.35*quality+.20*mom))
            for label, score in specifications:
                for top_k in (5, 10, 20):
                    for trend in (False, True):
                        local = mask & np.isfinite(score)
                        if trend: local &= trend_mask(p, 200)
                        weights = rank_long(p, score, signals, local, top_k, 63)
                        out.append(Variant(campaign_id, f"{name}__{label}__top{top_k}__trend{int(trend)}", p, weights, "open_to_next_open", 1, {"score":label,"top_k":top_k,"trend":trend,"liquidity":"top_half","weighting":"inverse_vol63"}))

    elif campaign_id == "CAM-0605":
        for name in ("sp500", "qqq"):
            p, signals = panels[name], month_end_indices(panels[name].dates)
            score, report = residual_momentum_score(p, f[name])
            for top_k in (3, 10, 20):
                for gate in ("liquid", "liquid_trend"):
                    mask = liquid_mask(p, .50) & np.isfinite(score) & (score > 0)
                    if gate == "liquid_trend": mask &= trend_mask(p, 200)
                    weights = rank_long(p, score, signals, mask, top_k, 63)
                    out.append(Variant(campaign_id, f"{name}__resmom__top{top_k}__{gate}", p, weights, "open_to_next_open", 1, {"top_k":top_k,"gate":gate,"source_fidelity":report}))

    elif campaign_id in {"CAM-0606", "CAM-0607"}:
        for name in ("sp500", "qqq", "etf"):
            p = panels[name]
            for lookback in (1, 2, 5):
                for top_k in ((1, 3, 5) if name == "etf" else (3, 10)):
                    for zmin in (.5, 1.0):
                        for trend in (False, True):
                            mask = liquid_mask(p, .50)
                            if trend: mask &= trend_mask(p, 200)
                            weights = cross_sectional_reversal(p, lookback, top_k, zmin, mask)
                            out.append(Variant(campaign_id, f"{name}__long_cheap_r{lookback}__top{top_k}__z{zmin:g}__trend{int(trend)}", p, weights, "open_to_close", 1, {"lookback":lookback,"top_k":top_k,"z_threshold":zmin,"trend":trend,"mode":"long_cheap_intraday"}))

    elif campaign_id in {"CAM-0608", "CAM-0609"}:
        for name in ("sp500", "qqq"):
            p = panels[name]
            for lookback in (1, 2, 5):
                if campaign_id == "CAM-0608":
                    raw, report = multiple_cluster_weights(p, panels["etf"], 126, lookback)
                else:
                    raw, report = weighted_regression_weights(p, f[name], lookback, 63)
                for top_k in (3, 10):
                    for gate in ("liquid", "liquid_trend"):
                        mask = liquid_mask(p, .50)
                        if gate == "liquid_trend": mask &= trend_mask(p, 200)
                        weights = concentrate_positive(raw, top_k, mask)
                        out.append(Variant(campaign_id, f"{name}__residual_r{lookback}__top{top_k}__{gate}", p, weights, "open_to_close", 1, {"lookback":lookback,"top_k":top_k,"gate":gate,"mode":"long_negative_residual",**report}))

    elif campaign_id in {"CAM-0610", "CAM-0611", "CAM-0612"}:
        configs: dict[str, tuple[tuple[int, ...], ...]] = {
            "CAM-0610": ((100,), (150,), (200,)),
            "CAM-0611": ((10, 30), (20, 50), (50, 200)),
            "CAM-0612": ((3, 10, 21), (5, 20, 50), (10, 50, 200)),
        }
        for name in ("sp500", "qqq", "etf"):
            p = panels[name]
            for cadence, signals in (("weekly", weekly_indices(p.dates)), ("monthly", month_end_indices(p.dates))):
                for windows in configs[campaign_id]:
                    mas = [moving_average(p, x) for x in windows]
                    if len(windows) == 1: condition = p.adj_close > mas[0]
                    elif len(windows) == 2: condition = mas[0] > mas[1]
                    else: condition = (mas[0] > mas[1]) & (mas[1] > mas[2])
                    for top_k in ((3, 10) if name != "etf" else (1, 3)):
                        for score_kind in ("momentum", "risk_adjusted"):
                            weights = active_trend_rank(p, condition, signals, top_k, score_kind)
                            out.append(Variant(campaign_id, f"{name}__ma{'_'.join(map(str,windows))}__{cadence}__top{top_k}__{score_kind}", p, weights, "open_to_next_open", 1, {"windows":list(windows),"cadence":cadence,"top_k":top_k,"rank":score_kind}))

    elif campaign_id == "CAM-0613":
        for name in ("sp500", "qqq", "etf"):
            p = panels[name]
            base, realized, report = pivot_weights(p, "long")
            dollar = pd.DataFrame(p.raw_close*p.volume).rolling(20, min_periods=10).mean().to_numpy(float)
            prior_dollar = np.vstack([np.full(p.n_symbols,np.nan),dollar[:-1]])
            vol_med = pd.DataFrame(p.volume).rolling(20,min_periods=10).median().to_numpy(float)
            for trend in (False, True):
                for volume in (False, True):
                    mask = liquid_mask(p,.35)
                    if trend: mask &= trend_mask(p,100)
                    if volume: mask &= p.volume > vol_med
                    weights = np.where(mask, base, 0.0)
                    out.append(Variant(campaign_id, f"{name}__pivot__trend{int(trend)}__volume{int(volume)}", p, weights, "return_override", 0, {"trend":trend,"volume_confirmation":volume,"liquidity":"top35pct",**report}, realized))

    elif campaign_id == "CAM-0614":
        for name in ("sp500", "qqq", "etf"):
            p = panels[name]
            for window in (20, 50, 100):
                reversal = channel_weights(p, window, "long")
                out.append(Variant(campaign_id, f"{name}__donchian{window}__reversal", p, reversal, "open_to_next_open", 1, {"window":window,"direction":"paper_reversal"}))
                prior_high = pd.DataFrame(p.adj_close).rolling(window,min_periods=window).max().shift(1).to_numpy(float)
                breakout = np.where(prior_high>0,p.adj_close/prior_high-1,np.nan)
                volume_ma = pd.DataFrame(p.volume).rolling(20,min_periods=15).mean().to_numpy(float)
                for top_k in ((3,10) if name != "etf" else (1,3)):
                    for volume in (False,True):
                        mask = liquid_mask(p,.50) & trend_mask(p,200) & (breakout>0)
                        if volume: mask &= p.volume > 1.25*volume_ma
                        weights = rank_long(p,breakout,np.arange(p.n_dates),mask,top_k,63)
                        out.append(Variant(campaign_id,f"{name}__donchian{window}__breakout__top{top_k}__volume{int(volume)}",p,weights,"open_to_next_open",1,{"window":window,"direction":"acknowledged_breakout","top_k":top_k,"volume_confirmation":volume}))

    elif campaign_id in {"CAM-0615", "CAM-0616"}:
        dollar_neutral = campaign_id == "CAM-0616"
        for name in ("sp500", "qqq", "etf"):
            p, signals = panels[name], month_end_indices(panels[name].dates)
            for horizon in (20,60,126):
                expected=trailing_return(p,horizon,0)
                for shrink in (.50,.75,.90):
                    raw=shrinkage_optimizer_weights(p,expected,signals,dollar_neutral=dollar_neutral,shrinkage=shrink)
                    for top_k in ((5,10) if name != "etf" else (3,5)):
                        weights=concentrate_positive(raw,top_k,liquid_mask(p,.50))
                        out.append(Variant(campaign_id,f"{name}__fullcov_s{int(shrink*100)}__mom{horizon}__positive_top{top_k}",p,weights,"open_to_next_open",1,{"horizon":horizon,"shrinkage":shrink,"top_k":top_k,"source_signed":dollar_neutral,"mode":"long_positive_sleeve","source_identity_note":"adapted long-only expression"}))

    elif campaign_id == "CAM-0617":
        for name in ("qqq","etf"):
            p=panels[name]
            for history,forecast in ((20,5),(60,5),(60,20),(120,20)):
                raw,report=alpha_combo_weights(p,history,forecast)
                for top_k in ((3,10) if name=="qqq" else (1,3,5)):
                    weights=concentrate_positive(raw,top_k,liquid_mask(p,.50))
                    out.append(Variant(campaign_id,f"{name}__alpha_M{history}_E{forecast}__positive_top{top_k}",p,weights,"open_to_next_open",1,{"history":history,"forecast":forecast,"top_k":top_k,"mode":"long_positive_alpha",**report}))

    elif campaign_id in {"CAM-0618","CAM-0619","CAM-0620"}:
        p=panels["etf"]
        base_mask=sector_panel_mask(p)
        spy=p.symbol_to_col["SPY"]
        for formation,skip in ((63,0),(126,0),(126,21),(252,21)):
            score=trailing_return(p,formation,skip)
            for cadence,signals in (("weekly",weekly_indices(p.dates)),("monthly",month_end_indices(p.dates))):
                for top_k in (1,3):
                    mask=base_mask & (score>0)
                    if campaign_id=="CAM-0619": mask &= trend_mask(p,100 if formation<=126 else 200)
                    weights=rank_long(p,score,signals,mask,top_k,63)
                    if campaign_id=="CAM-0620":
                        market_ma=moving_average(p,200)
                        fallback_col=p.symbol_to_col["BIL"]
                        raw=np.zeros_like(weights)
                        for i in signals:
                            if p.adj_close[i,spy]>market_ma[i,spy]: raw[i]=weights[i]
                            else: raw[i,fallback_col]=1.0
                        weights=forward_fill_signal_weights(raw,signals)
                    out.append(Variant(campaign_id,f"sector11__mom{formation}_skip{skip}__{cadence}__top{top_k}",p,weights,"open_to_next_open",1,{"formation":formation,"skip":skip,"cadence":cadence,"top_k":top_k,"absolute_momentum":True,"dual_market_gate":campaign_id=="CAM-0620"}))

    elif campaign_id == "CAM-0621":
        p=panels["etf"]
        spread=p.adj_high-p.adj_low
        ibs=np.where(spread>0,(p.adj_close-p.adj_low)/spread,np.nan)
        for top_k in (1,3,5):
            for threshold in (.10,.20,.30):
                for trend in (False,True):
                    mask=liquid_mask(p,.60) & np.isfinite(ibs) & (ibs<=threshold)
                    if trend: mask &= trend_mask(p,200)
                    weights=rank_weights(-ibs,mask,np.arange(p.n_dates),mode="long",top_k=top_k)
                    out.append(Variant(campaign_id,f"etf__ibs{int(threshold*100)}__top{top_k}__trend{int(trend)}",p,weights,"open_to_close",1,{"threshold":threshold,"top_k":top_k,"trend":trend,"entry":"next_open","exit":"same_close"}))

    elif campaign_id == "CAM-0622":
        p=panels["etf"]
        for risky in ("SPY","QQQ"):
            for target in (.08,.10,.12,.15):
                for window in (20,63,126):
                    for cadence in ("weekly","monthly"):
                        for threshold in (.10,.20):
                            for defense in (False,True):
                                weights=threshold_vol_target(p,risky,target,window,cadence,threshold,defense)
                                out.append(Variant(campaign_id,f"{risky}__target{int(target*100)}__vol{window}__{cadence}__thr{int(threshold*100)}__def{int(defense)}",p,weights,"open_to_next_open",1,{"risky":risky,"target":target,"window":window,"cadence":cadence,"rebalance_threshold":threshold,"trend_defense":defense,"cash":"BIL","margin":False}))

    elif campaign_id in {"CAM-0623","CAM-0624"}:
        for name in ("sp500","qqq"):
            p,signals=panels[name],month_end_indices(panels[name].dates)
            for top_k in (5,10,20):
                for gate in ("liquid","profitable","profitable_trend"):
                    mask=liquid_mask(p,.50)&np.isfinite(f[name].chs_logit)
                    if "profitable" in gate: mask &= f[name].profitability>0
                    if "trend" in gate: mask &= trend_mask(p,200)
                    base=rank_weights(f[name].chs_logit,mask,signals,mode="reversal_long",top_k=top_k,inverse_vol=trailing_vol(p,63))
                    targets=(None,) if campaign_id=="CAM-0623" else (.08,.10,.12,.15)
                    for target in targets:
                        weights=base if target is None else cash_vol_target_like(p,base,target,126,signals)
                        suffix="raw" if target is None else f"target{int(target*100)}"
                        out.append(Variant(campaign_id,f"{name}__chs_safe__top{top_k}__{gate}__{suffix}",p,weights,"open_to_next_open",1,{"top_k":top_k,"gate":gate,"vol_target":target,"weighting":"inverse_vol63","margin":False,"coverage":f[name].coverage}))

    if not out:
        raise RuntimeError(f"no deep variants for {campaign_id}")
    return out


def cash_vol_target_like(panel: Panel, base: np.ndarray, target: float, window: int, signals: np.ndarray) -> np.ndarray:
    returns = np.nan_to_num(panel.open_to_next_open_return, nan=0.0)
    sleeve = (base * returns).sum(axis=1)
    vol = pd.Series(sleeve).rolling(window, min_periods=max(20,window//2)).std(ddof=1).to_numpy(float)*np.sqrt(252.0)
    raw=np.zeros_like(base)
    for i in signals:
        if np.isfinite(vol[i]) and vol[i]>0: raw[i]=base[i]*min(1.0,target/vol[i])
    return forward_fill_signal_weights(raw,signals)
