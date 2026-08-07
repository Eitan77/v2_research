from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from baseline_strategies import (
    Variant,
    alpha_combo_weights,
    build_sue_scores,
    channel_weights,
    close_returns,
    direct_rule_weights,
    eligible,
    filtered_rank_weights,
    moving_average,
    multiple_cluster_weights,
    overlap_monthly_weights,
    pair_mean_reversion_weights,
    pivot_weights,
    rank_percentile,
    residual_momentum_score,
    scale_sleeve_to_vol,
    sector_panel_mask,
    single_cluster_weights,
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


def positive_sleeve(weights: np.ndarray) -> np.ndarray:
    out = np.clip(weights, 0.0, None)
    scale = out.sum(axis=1)
    valid = scale > 0
    out[valid] /= scale[valid, None]
    return out


def gate_by_benchmark(panel: Panel, weights: np.ndarray, window: int = 200) -> np.ndarray:
    symbol = "SPY" if "SPY" in panel.symbol_to_col else "QQQ"
    col = panel.symbol_to_col[symbol]
    ma = moving_average(panel, window)
    gate = np.isfinite(ma[:, col]) & (panel.adj_close[:, col] > ma[:, col])
    return weights * gate[:, None]


def expanded_mask(panel: Panel, *, unlevered_etf: bool = False) -> np.ndarray:
    mask = eligible(panel).copy()
    if unlevered_etf and panel.name == "etf":
        blocked = {"SOXL", "SOXS", "TQQQ", "SQQQ"}
        for symbol in blocked:
            col = panel.symbol_to_col.get(symbol)
            if col is not None:
                mask[:, col] = False
    return mask


def adapt_momentum(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        for formation, skip, hold, quantile in (
            (126, 0, 1, 0.10), (126, 21, 1, 0.20), (252, 0, 3, 0.10),
            (252, 21, 3, 0.20), (252, 21, 6, 0.10),
        ):
            score = trailing_return(panel, formation, skip)
            signals = month_end_indices(panel.dates)
            for universe in (("all", False), ("unlevered", True)) if panel.name == "etf" else (("all", False),):
                mask = expanded_mask(panel, unlevered_etf=universe[1])
                base = rank_weights(score, mask, signals, mode="long", quantile=quantile)
                weights = overlap_monthly_weights(base, signals, hold)
                for regime in (False, True):
                    candidate = gate_by_benchmark(panel, weights) if regime else weights
                    out.append(Variant(
                        campaign_id,
                        f"{panel.name}__mom{formation}_skip{skip}_hold{hold}_q{int(quantile*100)}__{universe[0]}__regime{int(regime)}",
                        panel, candidate, "open_to_next_open", 1,
                        {"formation": formation, "skip": skip, "hold_months": hold, "quantile": quantile,
                         "universe": universe[0], "benchmark_sma200_gate": regime, "mode": "long_cash"},
                    ))
    return out


def adapt_sue(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        panel = panels[name]
        score, report = build_sue_scores(panel, include_sec=True)
        signals = month_end_indices(panel.dates)
        for hold in (1, 3, 6):
            for quantile in (0.10, 0.20):
                base = rank_weights(score, eligible(panel) & np.isfinite(score), signals, mode="long", quantile=quantile)
                weights = overlap_monthly_weights(base, signals, hold)
                out.append(Variant(
                    campaign_id, f"{name}__sue_sec__hold{hold}__q{int(quantile*100)}__long",
                    panel, weights, "open_to_next_open", 1,
                    {"holding_months": hold, "quantile": quantile, "mode": "long", "source_repair": report},
                ))
    return out


def adapt_value(campaign_id: str, panels: dict[str, Panel], f: dict[str, FundamentalMatrices]) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        panel = panels[name]
        signals = month_end_indices(panel.dates)
        momentum = trailing_return(panel, 252, 21)
        base_mask = eligible(panel) & np.isfinite(f[name].book_to_price)
        masks = {
            "source": base_mask,
            "profitable": base_mask & (f[name].profitability > 0),
            "positive_momentum": base_mask & (momentum > 0),
        }
        for label, mask in masks.items():
            for hold in (1, 3, 6):
                for quantile in (0.10, 0.20):
                    base = rank_weights(f[name].book_to_price, mask, signals, mode="long", quantile=quantile)
                    weights = overlap_monthly_weights(base, signals, hold)
                    out.append(Variant(
                        campaign_id, f"{name}__btp__{label}__hold{hold}__q{int(quantile*100)}",
                        panel, weights, "open_to_next_open", 1,
                        {"confirmation": label, "hold_months": hold, "quantile": quantile,
                         "coverage": f[name].coverage},
                    ))
    return out


def adapt_low_vol(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        signals = month_end_indices(panel.dates)
        for window in (63, 126, 252):
            score = trailing_vol(panel, window)
            for hold in (1, 3, 6):
                for quantile in (0.10, 0.20):
                    base = rank_weights(score, eligible(panel), signals, mode="reversal_long", quantile=quantile)
                    weights = overlap_monthly_weights(base, signals, hold)
                    out.append(Variant(
                        campaign_id, f"{panel.name}__lowvol{window}__hold{hold}__q{int(quantile*100)}",
                        panel, weights, "open_to_next_open", 1,
                        {"volatility_window": window, "hold_months": hold, "quantile": quantile, "mode": "long"},
                    ))
    return out


def adapt_multifactor(campaign_id: str, panels: dict[str, Panel], f: dict[str, FundamentalMatrices]) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        panel = panels[name]
        signals = month_end_indices(panel.dates)
        mask = eligible(panel)
        mom = rank_percentile(trailing_return(panel, 252, 21), mask, signals)
        val = rank_percentile(f[name].book_to_price, mask, signals)
        for mom_weight in (0.25, 0.50, 0.75):
            score = mom_weight * mom + (1.0 - mom_weight) * val
            for confirmation in ("none", "profitable"):
                local_mask = mask & np.isfinite(score)
                if confirmation == "profitable":
                    local_mask &= f[name].profitability > 0
                for quantile in (0.10, 0.20):
                    weights = rank_weights(score, local_mask, signals, mode="long", quantile=quantile)
                    out.append(Variant(
                        campaign_id, f"{name}__mom{int(mom_weight*100)}_val{int((1-mom_weight)*100)}__{confirmation}__q{int(quantile*100)}",
                        panel, weights, "open_to_next_open", 1,
                        {"momentum_weight": mom_weight, "value_weight": 1.0-mom_weight,
                         "confirmation": confirmation, "quantile": quantile, "coverage": f[name].coverage},
                    ))
    return out


def adapt_residual_momentum(campaign_id: str, panels: dict[str, Panel], f: dict[str, FundamentalMatrices]) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        panel = panels[name]
        score, report = residual_momentum_score(panel, f[name])
        signals = month_end_indices(panel.dates)
        for quantile in (0.10, 0.20, 0.30):
            weights = rank_weights(score, eligible(panel) & np.isfinite(score), signals, mode="long", quantile=quantile)
            for regime in (False, True):
                candidate = gate_by_benchmark(panel, weights) if regime else weights
                out.append(Variant(
                    campaign_id, f"{name}__residual_mom__q{int(quantile*100)}__regime{int(regime)}",
                    panel, candidate, "open_to_next_open", 1,
                    {"quantile": quantile, "benchmark_sma200_gate": regime, "source_fidelity": report},
                ))
    return out


def adapt_pairs(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    settings = ((63, 1, .75), (63, 5, .80), (126, 1, .75), (126, 5, .85), (252, 5, .80), (252, 10, .85))
    for panel in panels.values():
        for formation, dislocation, corr in settings:
            weights, report = pair_mean_reversion_weights(panel, formation, dislocation, corr)
            out.append(Variant(
                campaign_id, f"{panel.name}__pairs_f{formation}_d{dislocation}_c{int(corr*100)}",
                panel, weights, "open_to_close", 1,
                {"signal_diagnostic": True, "short_rule": "intraday without protective stop; requires minute replay", **report},
            ))
    return out


def adapt_single_cluster(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        for lookback in (1, 2, 5, 10):
            raw = single_cluster_weights(panel, lookback)
            for mode, weights in (("long_short", raw), ("long", positive_sleeve(raw))):
                out.append(Variant(
                    campaign_id, f"{panel.name}__cluster_reversal_r{lookback}__{mode}",
                    panel, weights, "open_to_close", 1,
                    {"return_sessions": lookback, "mode": mode,
                     "short_rule": "intraday no-stop diagnostic" if mode == "long_short" else "none"},
                ))
    return out


def adapt_multiple_cluster(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        for formation, lookback in ((63, 1), (126, 2), (126, 5), (252, 5)):
            raw, report = multiple_cluster_weights(panels[name], panels["etf"], formation, lookback)
            for mode, weights in (("long_short", raw), ("long", positive_sleeve(raw))):
                out.append(Variant(
                    campaign_id, f"{name}__sector_clusters_f{formation}_r{lookback}__{mode}",
                    panels[name], weights, "open_to_close", 1,
                    {"mode": mode, "sector_etfs": list(SECTOR_ETFS),
                     "short_rule": "intraday no-stop diagnostic" if mode == "long_short" else "none", **report},
                ))
    return out


def adapt_weighted_regression(campaign_id: str, panels: dict[str, Panel], f: dict[str, FundamentalMatrices]) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        for lookback in (1, 2, 5, 10):
            raw, report = weighted_regression_weights(panels[name], f[name], lookback, 63)
            for mode, weights in (("long_short", raw), ("long", positive_sleeve(raw))):
                out.append(Variant(
                    campaign_id, f"{name}__weighted_residual_r{lookback}__{mode}",
                    panels[name], weights, "open_to_close", 1,
                    {"mode": mode, "short_rule": "intraday no-stop diagnostic" if mode == "long_short" else "none", **report},
                ))
    return out


def adapt_single_ma(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        mask = eligible(panel)
        for window in (50, 100, 150, 200, 250):
            ma = moving_average(panel, window)
            weights = direct_rule_weights(panel.adj_close, mask & np.isfinite(ma), long_condition=panel.adj_close > ma)
            out.append(Variant(campaign_id, f"{panel.name}__sma{window}__long", panel, weights, "open_to_next_open", 1,
                               {"window": window, "mode": "long_cash"}))
    return out


def adapt_two_ma(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        for fast_window, slow_window in ((5, 20), (10, 30), (20, 50), (50, 200)):
            fast, slow = moving_average(panel, fast_window), moving_average(panel, slow_window)
            weights = direct_rule_weights(fast, eligible(panel) & np.isfinite(fast) & np.isfinite(slow), long_condition=fast > slow)
            out.append(Variant(campaign_id, f"{panel.name}__sma{fast_window}_{slow_window}__long", panel, weights,
                               "open_to_next_open", 1, {"fast": fast_window, "slow": slow_window, "mode": "long_cash"}))
    return out


def adapt_three_ma(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        for windows in ((2, 5, 20), (3, 10, 21), (5, 20, 50), (10, 50, 200)):
            fast, mid, slow = (moving_average(panel, w) for w in windows)
            condition = (fast > mid) & (mid > slow)
            weights = direct_rule_weights(fast, eligible(panel) & np.isfinite(fast) & np.isfinite(mid) & np.isfinite(slow), long_condition=condition)
            out.append(Variant(campaign_id, f"{panel.name}__sma{'_'.join(map(str, windows))}__long", panel, weights,
                               "open_to_next_open", 1, {"windows": list(windows), "mode": "long_cash"}))
    return out


def adapt_pivot(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        weights, realized, report = pivot_weights(panel, "long")
        prev_high = np.vstack([np.full(panel.n_symbols, np.nan), panel.raw_high[:-1]])
        prev_low = np.vstack([np.full(panel.n_symbols, np.nan), panel.raw_low[:-1]])
        prev_close = np.vstack([np.full(panel.n_symbols, np.nan), panel.raw_close[:-1]])
        pivot = (prev_high + prev_low + prev_close) / 3.0
        atr = pd.DataFrame(panel.raw_high - panel.raw_low).rolling(20, min_periods=15).mean().to_numpy(float)
        strength = (panel.raw_open - pivot) / atr
        for threshold in (0.0, 0.25, 0.50):
            candidate = np.where(strength >= threshold, weights, 0.0)
            out.append(Variant(
                campaign_id, f"{panel.name}__pivot_target__long__atr{threshold:g}", panel, candidate,
                "return_override", 0,
                {"minimum_open_above_pivot_atr": threshold, "mode": "long", **report}, realized,
            ))
    return out


def adapt_channel(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    return [
        Variant(campaign_id, f"{panel.name}__donchian{window}__long", panel,
                channel_weights(panel, window, "long"), "open_to_next_open", 1,
                {"window": window, "mode": "long_cash", "signal": "lower boundary entry, upper boundary exit"})
        for panel in panels.values() for window in (10, 20, 50, 100)
    ]


def shrinkage_optimizer_weights(
    panel: Panel,
    expected: np.ndarray,
    signals: np.ndarray,
    *,
    dollar_neutral: bool,
    covariance_window: int = 126,
    shrinkage: float = 0.50,
) -> np.ndarray:
    returns = close_returns(panel)
    raw = np.zeros_like(expected)
    for i in signals:
        if i < covariance_window:
            continue
        window = returns[i-covariance_window+1:i+1]
        cols = np.flatnonzero(eligible(panel)[i] & np.isfinite(expected[i]) & (np.isfinite(window).sum(axis=0) >= int(.8*covariance_window)))
        if len(cols) < 2:
            continue
        x = window[:, cols].copy()
        means = np.nanmean(x, axis=0)
        missing = ~np.isfinite(x)
        x[missing] = np.take(means, np.where(missing)[1])
        x -= x.mean(axis=0, keepdims=True)
        variance = np.var(x, axis=0, ddof=1)
        valid = np.isfinite(variance) & (variance > 1e-10)
        cols, x, variance = cols[valid], x[:, valid], variance[valid]
        if len(cols) < 2:
            continue
        a_inv = 1.0 / (shrinkage * variance)
        u = x.T * np.sqrt((1.0-shrinkage) / max(1, covariance_window-1))
        middle = np.eye(covariance_window) + u.T @ (a_inv[:, None] * u)

        def inverse_apply(vector: np.ndarray) -> np.ndarray:
            av = a_inv * vector
            return av - (a_inv[:, None] * u) @ np.linalg.solve(middle, u.T @ av)

        candidate = inverse_apply(expected[i, cols])
        if dollar_neutral:
            inv_one = inverse_apply(np.ones(len(cols)))
            denom = float(inv_one.sum())
            if abs(denom) <= 1e-12:
                continue
            candidate -= inv_one * (candidate.sum() / denom)
        scale = float(np.abs(candidate).sum())
        if scale > 0:
            raw[i, cols] = candidate / scale
    return forward_fill_signal_weights(raw, signals)


def adapt_optimizer(campaign_id: str, panels: dict[str, Panel], dollar_neutral: bool) -> list[Variant]:
    out = []
    for panel in panels.values():
        signals = month_end_indices(panel.dates)
        for horizon in (5, 20, 60):
            expected = trailing_return(panel, horizon, 0)
            weights = shrinkage_optimizer_weights(panel, expected, signals, dollar_neutral=dollar_neutral)
            modes = (("dollar_neutral", weights),) if dollar_neutral else (("unconstrained", weights), ("long", positive_sleeve(weights)))
            for mode, candidate in modes:
                out.append(Variant(
                    campaign_id, f"{panel.name}__fullcov_shrink50__mom{horizon}__{mode}",
                    panel, candidate, "open_to_next_open", 1,
                    {"expected_horizon": horizon, "covariance_window": 126, "diagonal_shrinkage": .5,
                     "woodbury_full_covariance": True, "mode": mode,
                     "short_rule": "overnight signal diagnostic" if mode != "long" else "none"},
                ))
    return out


def adapt_alpha_combo(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for name in ("qqq", "etf"):
        for history, expected_days in ((20, 1), (20, 5), (60, 5), (60, 20), (120, 20)):
            weights, report = alpha_combo_weights(panels[name], history, expected_days)
            out.append(Variant(campaign_id, f"{name}__alpha_combo_M{history}_E{expected_days}", panels[name], weights,
                               "open_to_next_open", 1, report))
    return out


def adapt_sector_momentum(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    panel = panels["etf"]
    mask, signals = sector_panel_mask(panel), month_end_indices(panel.dates)
    out = []
    for formation in (126, 252):
        score = trailing_return(panel, formation, 0)
        for hold in (1, 3, 6):
            for top_k in (1, 3):
                base = rank_weights(score, mask, signals, mode="long", top_k=top_k)
                weights = overlap_monthly_weights(base, signals, hold)
                out.append(Variant(campaign_id, f"sector11__mom{formation}__hold{hold}__top{top_k}", panel, weights,
                                   "open_to_next_open", 1, {"sector_etfs": list(SECTOR_ETFS), "formation": formation,
                                                            "hold_months": hold, "top_k": top_k}))
    return out


def adapt_sector_ma(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    panel, out = panels["etf"], []
    mask, signals = sector_panel_mask(panel), month_end_indices(panel.dates)
    for formation in (126, 252):
        score = trailing_return(panel, formation, 0)
        for window in (100, 200):
            ma = moving_average(panel, window)
            for top_k in (1, 3):
                base = rank_weights(score, mask, signals, mode="long", top_k=top_k)
                gated = base * (panel.adj_close > ma)
                weights = forward_fill_signal_weights(gated, signals)
                out.append(Variant(campaign_id, f"sector11__mom{formation}__sma{window}__top{top_k}", panel, weights,
                                   "open_to_next_open", 1, {"sector_etfs": list(SECTOR_ETFS), "formation": formation,
                                                            "moving_average": window, "top_k": top_k}))
    return out


def adapt_dual_sector(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    panel, out = panels["etf"], []
    mask, signals = sector_panel_mask(panel), month_end_indices(panel.dates)
    spy_col = panel.symbol_to_col["SPY"]
    for formation in (126, 252):
        score = trailing_return(panel, formation, 0)
        base = rank_weights(score, mask, signals, mode="long", top_k=1)
        for window in (100, 200):
            ma = moving_average(panel, window)
            for fallback in ("BIL", "GLD", "TLT"):
                signal = np.zeros_like(base)
                fallback_col = panel.symbol_to_col[fallback]
                for i in signals:
                    if np.isfinite(ma[i, spy_col]) and panel.adj_close[i, spy_col] > ma[i, spy_col]:
                        signal[i] = base[i]
                    elif panel.member[i, fallback_col]:
                        signal[i, fallback_col] = 1.0
                weights = forward_fill_signal_weights(signal, signals)
                out.append(Variant(campaign_id, f"sector11__mom{formation}__sma{window}__fallback_{fallback}", panel,
                                   weights, "open_to_next_open", 1, {"formation": formation, "market_ma": window,
                                                                    "fallback": fallback, "sector_etfs": list(SECTOR_ETFS)}))
    return out


def adapt_ibs(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    panel, out = panels["etf"], []
    spread = panel.adj_high - panel.adj_low
    ibs = np.where(spread > 0, (panel.adj_close-panel.adj_low)/spread, np.nan)
    ma200 = moving_average(panel, 200)
    for threshold in (.10, .20, .30):
        for trend in (False, True):
            mask = eligible(panel) & np.isfinite(ibs)
            condition = ibs <= threshold
            if trend:
                condition &= panel.adj_close > ma200
            weights = direct_rule_weights(ibs, mask, long_condition=condition)
            out.append(Variant(campaign_id, f"etf__ibs_le{int(threshold*100)}__trend{int(trend)}__long", panel, weights,
                               "open_to_close", 1, {"ibs_threshold": threshold, "symbol_sma200_gate": trend, "mode": "long"}))
    return out


def cash_vol_target(panel: Panel, risky: str, cash: str, target: float, window: int, rebalance: str) -> np.ndarray:
    risky_col, cash_col = panel.symbol_to_col[risky], panel.symbol_to_col[cash]
    vol = trailing_vol(panel, window)
    signals = weekly_indices(panel.dates) if rebalance == "weekly" else month_end_indices(panel.dates)
    raw = np.zeros_like(panel.adj_close)
    for i in signals:
        sigma = vol[i, risky_col]
        if np.isfinite(sigma) and sigma > 0 and panel.member[i, risky_col] and panel.member[i, cash_col]:
            risky_weight = min(1.0, target/sigma)
            raw[i, risky_col], raw[i, cash_col] = risky_weight, 1.0-risky_weight
    return forward_fill_signal_weights(raw, signals)


def adapt_vol_target(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    panel, out = panels["etf"], []
    for risky in ("SPY", "QQQ"):
        for target in (.08, .10, .12, .15):
            for window in (63, 126, 252):
                for rebalance in ("weekly", "monthly"):
                    weights = cash_vol_target(panel, risky, "BIL", target, window, rebalance)
                    out.append(Variant(campaign_id, f"{risky}__target{int(target*100)}__vol{window}__{rebalance}__BIL",
                                       panel, weights, "open_to_next_open", 1,
                                       {"risky": risky, "cash_asset": "BIL", "target": target, "window": window,
                                        "rebalance": rebalance, "margin": "none", "maximum_risky_weight": 1.0}))
    return out


def adapt_distress(campaign_id: str, panels: dict[str, Panel], f: dict[str, FundamentalMatrices]) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        panel, signals = panels[name], month_end_indices(panels[name].dates)
        score = f[name].chs_logit
        for quantile in (.10, .20, .30):
            for confirmation in ("none", "profitable"):
                mask = eligible(panel) & np.isfinite(score)
                if confirmation == "profitable":
                    mask &= f[name].profitability > 0
                weights = rank_weights(score, mask, signals, mode="reversal_long", quantile=quantile)
                out.append(Variant(campaign_id, f"{name}__chs_safe__q{int(quantile*100)}__{confirmation}", panel, weights,
                                   "open_to_next_open", 1, {"quantile": quantile, "confirmation": confirmation,
                                                            "coverage": f[name].coverage, "mode": "long_safest"}))
    return out


def adapt_distress_risk(campaign_id: str, panels: dict[str, Panel], f: dict[str, FundamentalMatrices]) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        panel, signals = panels[name], month_end_indices(panels[name].dates)
        base = rank_weights(f[name].chs_logit, eligible(panel) & np.isfinite(f[name].chs_logit), signals,
                            mode="reversal_long", quantile=.20)
        for target in (.08, .10, .12, .15):
            for window in (126, 252):
                weights = scale_sleeve_to_vol(panel, base, target, window, signals)
                out.append(Variant(campaign_id, f"{name}__chs_safe_q20__target{int(target*100)}__vol{window}", panel,
                                   weights, "open_to_next_open", 1, {"quantile": .20, "target": target,
                                                                    "volatility_window": window, "margin": "none"}))
    return out


def build_adaptations(campaign_id: str, panels: dict[str, Panel], f: dict[str, FundamentalMatrices]) -> list[Variant]:
    dispatch: dict[str, Any] = {
        "CAM-0600": lambda: adapt_momentum(campaign_id, panels),
        "CAM-0601": lambda: adapt_sue(campaign_id, panels),
        "CAM-0602": lambda: adapt_value(campaign_id, panels, f),
        "CAM-0603": lambda: adapt_low_vol(campaign_id, panels),
        "CAM-0604": lambda: adapt_multifactor(campaign_id, panels, f),
        "CAM-0605": lambda: adapt_residual_momentum(campaign_id, panels, f),
        "CAM-0606": lambda: adapt_pairs(campaign_id, panels),
        "CAM-0607": lambda: adapt_single_cluster(campaign_id, panels),
        "CAM-0608": lambda: adapt_multiple_cluster(campaign_id, panels),
        "CAM-0609": lambda: adapt_weighted_regression(campaign_id, panels, f),
        "CAM-0610": lambda: adapt_single_ma(campaign_id, panels),
        "CAM-0611": lambda: adapt_two_ma(campaign_id, panels),
        "CAM-0612": lambda: adapt_three_ma(campaign_id, panels),
        "CAM-0613": lambda: adapt_pivot(campaign_id, panels),
        "CAM-0614": lambda: adapt_channel(campaign_id, panels),
        "CAM-0615": lambda: adapt_optimizer(campaign_id, panels, False),
        "CAM-0616": lambda: adapt_optimizer(campaign_id, panels, True),
        "CAM-0617": lambda: adapt_alpha_combo(campaign_id, panels),
        "CAM-0618": lambda: adapt_sector_momentum(campaign_id, panels),
        "CAM-0619": lambda: adapt_sector_ma(campaign_id, panels),
        "CAM-0620": lambda: adapt_dual_sector(campaign_id, panels),
        "CAM-0621": lambda: adapt_ibs(campaign_id, panels),
        "CAM-0622": lambda: adapt_vol_target(campaign_id, panels),
        "CAM-0623": lambda: adapt_distress(campaign_id, panels, f),
        "CAM-0624": lambda: adapt_distress_risk(campaign_id, panels, f),
    }
    variants = dispatch[campaign_id]()
    if not variants:
        raise RuntimeError(f"{campaign_id} produced no adaptations")
    return variants
