from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from fundamentals import FundamentalMatrices, FundamentalStore, build_fundamental_matrices
from suite_core import (
    CAMPAIGNS,
    CATALOG,
    CUTOFF,
    ETF_SYMBOLS,
    SECTOR_ETFS,
    Panel,
    forward_fill_signal_weights,
    month_end_indices,
    rank_weights,
    trailing_return,
    trailing_vol,
    weekly_indices,
)


@dataclass
class Variant:
    campaign_id: str
    variant_id: str
    panel: Panel
    weights: np.ndarray
    holding: str
    execution_lag: int
    metadata: dict[str, Any]
    return_override: np.ndarray | None = None


def eligible(panel: Panel) -> np.ndarray:
    return (
        panel.member
        & np.isfinite(panel.adj_close)
        & np.isfinite(panel.adj_open)
        & (panel.adj_close > 1.0)
    )


def overlap_monthly_weights(
    filled_weights: np.ndarray,
    signal_indices: np.ndarray,
    hold_months: int,
) -> np.ndarray:
    signal_only = np.zeros_like(filled_weights)
    signal_only[signal_indices] = filled_weights[signal_indices]
    combined = np.zeros_like(filled_weights)
    for k, idx in enumerate(signal_indices):
        starts = signal_indices[max(0, k - hold_months + 1) : k + 1]
        combined[idx] = signal_only[starts].sum(axis=0) / float(hold_months)
    return forward_fill_signal_weights(combined, signal_indices)


def direct_rule_weights(
    score: np.ndarray,
    mask: np.ndarray,
    *,
    long_condition: np.ndarray,
    short_condition: np.ndarray | None = None,
    gross: float = 1.0,
) -> np.ndarray:
    out = np.zeros_like(score, dtype=float)
    for i in range(len(out)):
        longs = np.flatnonzero(mask[i] & long_condition[i])
        shorts = (
            np.flatnonzero(mask[i] & short_condition[i])
            if short_condition is not None
            else np.asarray([], dtype=int)
        )
        if len(longs) and len(shorts):
            out[i, longs] = 0.5 * gross / len(longs)
            out[i, shorts] = -0.5 * gross / len(shorts)
        elif len(longs):
            out[i, longs] = gross / len(longs)
        elif len(shorts):
            out[i, shorts] = -gross / len(shorts)
    return out


def build_sue_scores(panel: Panel, *, include_sec: bool = False) -> tuple[np.ndarray, dict[str, Any]]:
    with duckdb.connect(str(CATALOG), read_only=True) as con:
        events = con.execute(
            """
            SELECT symbol,
                   try_cast(earnings_datetime AS TIMESTAMPTZ) AS event_ts,
                   reported_eps
            FROM earnings
            WHERE try_cast(earnings_datetime AS TIMESTAMPTZ)
                    < TIMESTAMPTZ '2026-05-01 07:00:00+00'
              AND reported_eps IS NOT NULL
            ORDER BY symbol, event_ts
            """
        ).df()
    events["event_ts"] = pd.to_datetime(events["event_ts"], utc=True)
    events["event_date"] = (
        events["event_ts"].dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    )
    sec_symbols: set[str] = set()
    sec_quarters = 0
    if include_sec:
        facts_path = CAMPAIGNS / "CAM-0600" / "artifacts" / "shared" / "fundamental_facts.parquet"
        facts = pd.read_parquet(
            facts_path,
            columns=["symbol", "tag", "value", "filed", "period_start", "period_end", "duration_days", "form"],
        )
        facts = facts[
            (facts["tag"] == "EarningsPerShareDiluted")
            & facts["symbol"].notna()
            & facts["value"].notna()
        ].copy()
        facts["symbol"] = facts["symbol"].astype(str)
        facts["filed"] = pd.to_datetime(facts["filed"])
        facts["period_start"] = pd.to_datetime(facts["period_start"])
        facts["period_end"] = pd.to_datetime(facts["period_end"])
        facts["duration_days"] = pd.to_numeric(facts["duration_days"], errors="coerce")
        facts = facts[(facts["filed"] <= CUTOFF) & facts["form"].isin(["10-Q", "10-K", "10-Q/A", "10-K/A"])]
        sec_events: list[dict[str, Any]] = []
        for symbol, group in facts.groupby("symbol", sort=False):
            direct = group[group["duration_days"].between(60, 120, inclusive="both")].copy()
            direct = direct.sort_values(["period_end", "filed"]).drop_duplicates("period_end", keep="first")
            annual = group[group["duration_days"].between(300, 400, inclusive="both")].copy()
            annual = annual.sort_values(["period_end", "filed"]).drop_duplicates("period_end", keep="first")
            quarters = [
                {
                    "period_end": pd.Timestamp(row.period_end),
                    "filed": pd.Timestamp(row.filed),
                    "eps": float(row.value),
                    "source": "sec_direct_quarter",
                }
                for row in direct.itertuples(index=False)
                if pd.notna(row.period_end) and pd.notna(row.filed) and np.isfinite(float(row.value))
            ]
            for row in annual.itertuples(index=False):
                if pd.isna(row.period_start) or pd.isna(row.period_end) or pd.isna(row.filed):
                    continue
                annual_start = pd.Timestamp(row.period_start)
                annual_end = pd.Timestamp(row.period_end)
                prior = [q for q in quarters if annual_start < q["period_end"] < annual_end]
                prior = sorted(prior, key=lambda q: q["period_end"])[-3:]
                if len(prior) != 3:
                    continue
                q4 = float(row.value) - sum(float(q["eps"]) for q in prior)
                if np.isfinite(q4):
                    quarters.append({
                        "period_end": annual_end,
                        "filed": pd.Timestamp(row.filed),
                        "eps": q4,
                        "source": "sec_q4_annual_less_q1_q2_q3",
                    })
            qframe = pd.DataFrame(quarters)
            if qframe.empty:
                continue
            qframe = qframe.sort_values(["period_end", "filed"]).drop_duplicates("period_end", keep="first")
            if len(qframe) < 12:
                continue
            sec_symbols.add(str(symbol))
            sec_quarters += len(qframe)
            for row in qframe.itertuples(index=False):
                sec_events.append({
                    "symbol": str(symbol),
                    "event_date": pd.Timestamp(row.filed).normalize(),
                    "reported_eps": float(row.eps),
                    "period_end": pd.Timestamp(row.period_end),
                    "event_source": str(row.source),
                })
        if sec_events:
            sec_frame = pd.DataFrame(sec_events)
            # SEC sequences supersede the sparse vendor sequence symbol by symbol.
            events = events[~events["symbol"].astype(str).isin(sec_symbols)].copy()
            events["period_end"] = pd.NaT
            events["event_source"] = "catalog_earnings"
            events = pd.concat([events, sec_frame], ignore_index=True, sort=False)
    scores = np.full(panel.adj_close.shape, np.nan)
    symbol_to_col = panel.symbol_to_col
    usable_events = 0
    usable_symbols = set()
    for symbol, group in events.groupby("symbol", sort=False):
        symbol = str(symbol)
        if symbol not in symbol_to_col:
            continue
        order_col = "period_end" if "period_end" in group and group["period_end"].notna().any() else "event_date"
        g = group.sort_values([order_col, "event_date"]).drop_duplicates(order_col, keep="last")
        eps = g["reported_eps"].to_numpy(float)
        dates = g["event_date"].to_numpy(dtype="datetime64[ns]")
        sue_dates = []
        sue_values = []
        changes = np.full(len(eps), np.nan)
        for k in range(4, len(eps)):
            changes[k] = eps[k] - eps[k - 4]
            history = changes[max(4, k - 7) : k + 1]
            if len(history) < 8 or not np.isfinite(history).all():
                continue
            sigma = float(np.std(history, ddof=1))
            if sigma <= 0:
                continue
            sue_dates.append(dates[k])
            sue_values.append(float(changes[k] / sigma))
        if not sue_dates:
            continue
        usable_symbols.add(symbol)
        usable_events += len(sue_dates)
        sue_dates_np = np.asarray(sue_dates, dtype="datetime64[ns]")
        sue_values_np = np.asarray(sue_values, dtype=float)
        col = symbol_to_col[symbol]
        for i, current in enumerate(panel.dates.to_numpy(dtype="datetime64[ns]")):
            k = int(np.searchsorted(sue_dates_np, current, side="left"))
            if k > 0:
                scores[i, col] = sue_values_np[k - 1]
    report = {
        "source_rows": int(len(events)),
        "source_symbols": int(events["symbol"].nunique()),
        "usable_sue_events": int(usable_events),
        "usable_sue_symbols": int(len(usable_symbols)),
        "score_cells": int(np.isfinite(scores).sum()),
        "latest_event_date": str(events["event_date"].max().date()) if len(events) else None,
        "holdout_rows_loaded": int((events["event_date"] >= pd.Timestamp("2026-05-01")).sum()),
        "sec_extension_enabled": include_sec,
        "sec_symbols": int(len(sec_symbols)),
        "sec_quarter_rows": int(sec_quarters),
        "q4_derivation": "annual diluted EPS less first three direct quarterly diluted EPS values",
    }
    return scores, report


def monthly_factor_returns(
    panel: Panel,
    fundamentals: FundamentalMatrices,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ends = month_end_indices(panel.dates)
    tri = panel.total_return_index
    monthly = np.full((len(ends), panel.n_symbols), np.nan)
    for k in range(1, len(ends)):
        prev = ends[k - 1]
        cur = ends[k]
        valid = np.isfinite(tri[cur]) & np.isfinite(tri[prev]) & (tri[prev] > 0)
        monthly[k, valid] = tri[cur, valid] / tri[prev, valid] - 1.0
    benchmark = panel.symbol_to_col.get("SPY", panel.symbol_to_col.get("QQQ", 0))
    mkt = monthly[:, benchmark].copy()
    smb = np.full(len(ends), np.nan)
    hml = np.full(len(ends), np.nan)
    for k in range(1, len(ends)):
        signal = ends[k - 1]
        current_returns = monthly[k]
        mask = panel.member[signal] & np.isfinite(current_returns)
        cols = np.flatnonzero(mask)
        if len(cols) < 20:
            continue
        caps = fundamentals.market_cap[signal, cols]
        values = fundamentals.book_to_price[signal, cols]
        cap_cols = cols[np.isfinite(caps)]
        value_cols = cols[np.isfinite(values)]
        if len(cap_cols) >= 20:
            ordered = cap_cols[np.argsort(fundamentals.market_cap[signal, cap_cols])]
            n = max(1, len(ordered) // 3)
            smb[k] = float(np.nanmean(current_returns[ordered[:n]]) - np.nanmean(current_returns[ordered[-n:]]))
        if len(value_cols) >= 20:
            ordered = value_cols[np.argsort(fundamentals.book_to_price[signal, value_cols])]
            n = max(1, len(ordered) // 3)
            hml[k] = float(np.nanmean(current_returns[ordered[-n:]]) - np.nanmean(current_returns[ordered[:n]]))
    return monthly, np.column_stack([mkt, smb, hml]), ends


def residual_momentum_score(
    panel: Panel,
    fundamentals: FundamentalMatrices,
) -> tuple[np.ndarray, dict[str, Any]]:
    monthly, factors, ends = monthly_factor_returns(panel, fundamentals)
    scores = np.full(panel.adj_close.shape, np.nan)
    fitted_symbols = set()
    fitted_cells = 0
    for m in range(37, len(ends)):
        regression_months = np.arange(m - 36, m)
        formation_months = np.arange(m - 12, m)
        f_reg = factors[regression_months]
        for col in np.flatnonzero(panel.member[ends[m]]):
            y = monthly[regression_months, col]
            valid = np.isfinite(y) & np.isfinite(f_reg).all(axis=1)
            if valid.sum() < 24:
                continue
            X = np.column_stack([np.ones(valid.sum()), f_reg[valid]])
            beta = np.linalg.lstsq(X, y[valid], rcond=None)[0]
            y_form = monthly[formation_months, col]
            f_form = factors[formation_months]
            valid_form = np.isfinite(y_form) & np.isfinite(f_form).all(axis=1)
            if valid_form.sum() < 8:
                continue
            residual = y_form[valid_form] - f_form[valid_form] @ beta[1:]
            sigma = float(np.std(residual, ddof=1))
            if sigma <= 0:
                continue
            scores[ends[m], col] = float(np.mean(residual) / sigma)
            fitted_symbols.add(str(panel.symbols[col]))
            fitted_cells += 1
    return scores, {
        "factor_definition": "Causal panel proxies: benchmark MKT, bottom-minus-top market-cap SMB, high-minus-low book-to-price HML.",
        "regression_months": 36,
        "formation_months": 12,
        "skip_months": 1,
        "score_cells": fitted_cells,
        "symbols": len(fitted_symbols),
    }


def baseline_price_momentum(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        score = trailing_return(panel, 252, 21)
        signals = month_end_indices(panel.dates)
        for mode in ("long", "long_short"):
            weights = rank_weights(score, eligible(panel), signals, mode=mode, quantile=0.10)
            out.append(Variant(
                campaign_id, f"{panel.name}__12m_skip1__{mode}", panel, weights,
                "open_to_next_open", 1,
                {"formation_sessions": 252, "skip_sessions": 21, "holding": "one_month", "mode": mode},
            ))
    return out


def baseline_earnings_momentum(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        panel = panels[name]
        score, report = build_sue_scores(panel)
        signals = month_end_indices(panel.dates)
        for mode in ("long", "long_short"):
            base = rank_weights(score, eligible(panel) & np.isfinite(score), signals, mode=mode, quantile=0.10)
            weights = overlap_monthly_weights(base, signals, 6)
            out.append(Variant(
                campaign_id, f"{name}__sue8__hold6__{mode}", panel, weights,
                "open_to_next_open", 1, {"sue_readiness": report, "mode": mode, "holding_months": 6},
            ))
    return out


def baseline_value(
    campaign_id: str,
    panels: dict[str, Panel],
    fundamental: dict[str, FundamentalMatrices],
) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        panel = panels[name]
        score = fundamental[name].book_to_price
        signals = month_end_indices(panel.dates)
        for mode in ("long", "long_short"):
            weights = rank_weights(score, eligible(panel) & np.isfinite(score), signals, mode=mode, quantile=0.10)
            out.append(Variant(
                campaign_id, f"{name}__book_to_price__{mode}", panel, weights,
                "open_to_next_open", 1,
                {"mode": mode, "rebalance": "monthly", "coverage": fundamental[name].coverage},
            ))
    return out


def baseline_low_vol(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        signals = month_end_indices(panel.dates)
        for window in (126, 252):
            score = trailing_vol(panel, window)
            for mode in ("reversal_long", "reversal_long_short"):
                base = rank_weights(score, eligible(panel), signals, mode=mode, quantile=0.10)
                hold_months = 6 if window == 126 else 12
                weights = overlap_monthly_weights(base, signals, hold_months)
                out.append(Variant(
                    campaign_id, f"{panel.name}__vol{window}__hold{hold_months}__{mode}",
                    panel, weights, "open_to_next_open", 1,
                    {"volatility_window": window, "holding_months": hold_months, "mode": mode},
                ))
    return out


def rank_percentile(score: np.ndarray, mask: np.ndarray, signal_indices: np.ndarray) -> np.ndarray:
    out = np.full_like(score, np.nan)
    for i in signal_indices:
        cols = np.flatnonzero(mask[i] & np.isfinite(score[i]))
        if len(cols) < 2:
            continue
        order = cols[np.argsort(score[i, cols], kind="stable")]
        out[i, order] = np.linspace(0.0, 1.0, len(order))
    return out


def baseline_multifactor(
    campaign_id: str,
    panels: dict[str, Panel],
    fundamental: dict[str, FundamentalMatrices],
) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        panel = panels[name]
        signals = month_end_indices(panel.dates)
        mask = eligible(panel)
        momentum = trailing_return(panel, 252, 21)
        value = fundamental[name].book_to_price
        mom_rank = rank_percentile(momentum, mask, signals)
        val_rank = rank_percentile(value, mask, signals)
        combined = (mom_rank + val_rank) / 2.0
        for mode in ("long", "long_short"):
            weights = rank_weights(combined, mask & np.isfinite(combined), signals, mode=mode, quantile=0.10)
            out.append(Variant(
                campaign_id, f"{name}__average_momentum_value_ranks__{mode}", panel,
                weights, "open_to_next_open", 1,
                {"combination": "equal_average_demeaned_rank_equivalent", "mode": mode, "coverage": fundamental[name].coverage},
            ))
    return out


def baseline_residual_momentum(
    campaign_id: str,
    panels: dict[str, Panel],
    fundamental: dict[str, FundamentalMatrices],
) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        panel = panels[name]
        score, report = residual_momentum_score(panel, fundamental[name])
        signals = month_end_indices(panel.dates)
        for mode in ("long", "long_short"):
            weights = rank_weights(score, eligible(panel) & np.isfinite(score), signals, mode=mode, quantile=0.10)
            out.append(Variant(
                campaign_id, f"{name}__ff3proxy_residual_risk_adjusted__{mode}",
                panel, weights, "open_to_next_open", 1,
                {"mode": mode, "source_fidelity": report},
            ))
    return out


def close_returns(panel: Panel) -> np.ndarray:
    tri = panel.total_return_index
    out = np.full_like(tri, np.nan)
    valid = np.isfinite(tri[1:]) & np.isfinite(tri[:-1]) & (tri[:-1] > 0)
    tmp = np.full_like(tri[1:], np.nan)
    tmp[valid] = tri[1:][valid] / tri[:-1][valid] - 1.0
    out[1:] = tmp
    return out


def normalize_cross_section(raw: np.ndarray, mask: np.ndarray, gross: float = 1.0) -> np.ndarray:
    out = np.zeros_like(raw)
    for i in range(len(out)):
        cols = np.flatnonzero(mask[i] & np.isfinite(raw[i]))
        if not len(cols):
            continue
        values = raw[i, cols]
        scale = float(np.abs(values).sum())
        if scale > 0:
            out[i, cols] = values / scale * gross
    return out


def pair_mean_reversion_weights(
    panel: Panel,
    formation: int = 126,
    dislocation: int = 5,
    min_corr: float = 0.80,
) -> tuple[np.ndarray, dict[str, Any]]:
    ret = close_returns(panel)
    log_tri = np.log(np.where(panel.total_return_index > 0, panel.total_return_index, np.nan))
    signals = month_end_indices(panel.dates)
    month_for_day = np.searchsorted(signals, np.arange(panel.n_dates), side="right") - 1
    pairs_by_month: dict[int, list[tuple[int, int, float]]] = {}
    pair_counts = []
    for k, idx in enumerate(signals):
        if idx < formation:
            continue
        cols = np.flatnonzero(panel.member[idx] & np.isfinite(ret[idx]))
        if len(cols) < 2:
            continue
        window = ret[idx - formation + 1 : idx + 1, cols]
        valid_cols = np.isfinite(window).sum(axis=0) >= int(formation * 0.8)
        cols = cols[valid_cols]
        window = window[:, valid_cols]
        if len(cols) < 2:
            continue
        frame = pd.DataFrame(window).corr(min_periods=int(formation * 0.8)).to_numpy(float).copy()
        np.fill_diagonal(frame, -np.inf)
        unused = set(range(len(cols)))
        pairs = []
        while len(unused) >= 2:
            best = None
            best_corr = min_corr
            unused_list = sorted(unused)
            for a_pos, a in enumerate(unused_list):
                candidates = unused_list[a_pos + 1 :]
                if not candidates:
                    continue
                vals = frame[a, candidates]
                j = int(np.nanargmax(vals)) if np.isfinite(vals).any() else -1
                if j >= 0 and float(vals[j]) > best_corr:
                    best_corr = float(vals[j])
                    best = (a, candidates[j])
            if best is None:
                break
            a, b = best
            pairs.append((int(cols[a]), int(cols[b]), best_corr))
            unused.remove(a)
            unused.remove(b)
        pairs_by_month[k] = pairs
        pair_counts.append(len(pairs))
    raw = np.zeros(panel.adj_close.shape)
    active_pair_days = 0
    for i in range(max(formation, dislocation), panel.n_dates):
        k = int(month_for_day[i])
        pairs = pairs_by_month.get(k, [])
        if not pairs:
            continue
        pair_gross = 1.0 / len(pairs)
        for a, b, _ in pairs:
            if not np.isfinite(log_tri[i, a]) or not np.isfinite(log_tri[i - dislocation, a]):
                continue
            if not np.isfinite(log_tri[i, b]) or not np.isfinite(log_tri[i - dislocation, b]):
                continue
            ra = log_tri[i, a] - log_tri[i - dislocation, a]
            rb = log_tri[i, b] - log_tri[i - dislocation, b]
            residual = 0.5 * (ra - rb)
            if abs(residual) <= 1e-12:
                continue
            raw[i, a] += -np.sign(residual) * 0.5 * pair_gross
            raw[i, b] += np.sign(residual) * 0.5 * pair_gross
            active_pair_days += 1
    return raw, {
        "formation_sessions": formation,
        "dislocation_sessions": dislocation,
        "minimum_correlation": min_corr,
        "months_with_pairs": len(pairs_by_month),
        "median_pairs": float(np.median(pair_counts)) if pair_counts else 0.0,
        "active_pair_day_units": active_pair_days,
    }


def baseline_pairs(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        weights, report = pair_mean_reversion_weights(panel)
        out.append(Variant(
            campaign_id, f"{panel.name}__corr126__dislocation5__pair_dollar_neutral",
            panel, weights, "open_to_close", 1,
            {"signal_diagnostic": True, "short_rule": "intraday_next_open_to_close_without_execution_qualified_stop", **report},
        ))
    return out


def single_cluster_weights(panel: Panel, lookback: int = 1) -> np.ndarray:
    score = trailing_return(panel, lookback, 0)
    raw = np.full_like(score, np.nan)
    for i in range(lookback, panel.n_dates):
        cols = np.flatnonzero(eligible(panel)[i] & np.isfinite(score[i]))
        if len(cols) < 2:
            continue
        residual = score[i, cols] - np.mean(score[i, cols])
        raw[i, cols] = -residual
    return normalize_cross_section(raw, panel.member & np.isfinite(raw))


def baseline_single_cluster(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    return [
        Variant(
            campaign_id, f"{panel.name}__cluster_all__return1__negative_demeaned",
            panel, single_cluster_weights(panel, 1), "open_to_close", 1,
            {"cluster": "entire_applicable_panel", "return_sessions": 1, "short_rule": "intraday_signal_diagnostic"},
        )
        for panel in panels.values()
    ]


def align_sector_returns(panel: Panel, etf: Panel) -> tuple[np.ndarray, np.ndarray]:
    sector_cols = [etf.symbol_to_col[s] for s in SECTOR_ETFS if s in etf.symbol_to_col]
    etf_ret = close_returns(etf)[:, sector_cols]
    etf_dates = {d: i for i, d in enumerate(etf.dates)}
    aligned = np.full((panel.n_dates, len(sector_cols)), np.nan)
    for i, d in enumerate(panel.dates):
        j = etf_dates.get(d)
        if j is not None:
            aligned[i] = etf_ret[j]
    return aligned, np.asarray(sector_cols, dtype=int)


def multiple_cluster_weights(
    panel: Panel,
    etf: Panel,
    formation: int = 126,
    lookback: int = 1,
) -> tuple[np.ndarray, dict[str, Any]]:
    ret = close_returns(panel)
    sector_ret, sector_cols = align_sector_returns(panel, etf)
    signals = month_end_indices(panel.dates)
    month_for_day = np.searchsorted(signals, np.arange(panel.n_dates), side="right") - 1
    maps: dict[int, np.ndarray] = {}
    assigned_cells = 0
    for k, idx in enumerate(signals):
        if idx < formation:
            continue
        assignments = np.full(panel.n_symbols, -1, dtype=int)
        cols = np.flatnonzero(panel.member[idx])
        for col in cols:
            y = ret[idx - formation + 1 : idx + 1, col]
            best_sector = -1
            best_corr = -np.inf
            for s in range(sector_ret.shape[1]):
                x = sector_ret[idx - formation + 1 : idx + 1, s]
                valid = np.isfinite(x) & np.isfinite(y)
                if valid.sum() < int(formation * 0.8):
                    continue
                corr = float(np.corrcoef(x[valid], y[valid])[0, 1])
                if np.isfinite(corr) and corr > best_corr:
                    best_corr = corr
                    best_sector = s
            assignments[col] = best_sector
            assigned_cells += best_sector >= 0
        maps[k] = assignments
    raw = np.full(panel.adj_close.shape, np.nan)
    score = trailing_return(panel, lookback, 0)
    for i in range(max(formation, lookback), panel.n_dates):
        assignments = maps.get(int(month_for_day[i]))
        if assignments is None:
            continue
        for cluster in range(len(sector_cols)):
            cols = np.flatnonzero(
                (assignments == cluster)
                & panel.member[i]
                & np.isfinite(score[i])
            )
            if len(cols) < 2:
                continue
            residual = score[i, cols] - np.mean(score[i, cols])
            raw[i, cols] = -residual
    weights = normalize_cross_section(raw, panel.member & np.isfinite(raw))
    return weights, {
        "cluster_definition": "causal highest trailing correlation to available sector ETFs",
        "formation_sessions": formation,
        "clusters": len(sector_cols),
        "assignment_cells": int(assigned_cells),
        "return_sessions": lookback,
    }


def baseline_multiple_cluster(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    etf = panels["etf"]
    out = []
    for name in ("sp500", "qqq"):
        weights, report = multiple_cluster_weights(panels[name], etf)
        out.append(Variant(
            campaign_id, f"{name}__sector_proxy_clusters__return1",
            panels[name], weights, "open_to_close", 1,
            {"short_rule": "intraday_signal_diagnostic", **report},
        ))
    return out


def rolling_beta(panel: Panel, window: int = 126) -> np.ndarray:
    ret = pd.DataFrame(close_returns(panel), index=panel.dates)
    benchmark = panel.symbol_to_col.get("SPY", panel.symbol_to_col.get("QQQ", 0))
    market = ret.iloc[:, benchmark]
    cov = ret.rolling(window, min_periods=int(window * 0.8)).cov(market)
    var = market.rolling(window, min_periods=int(window * 0.8)).var()
    return cov.div(var, axis=0).to_numpy(float)


def weighted_regression_weights(
    panel: Panel,
    fundamentals: FundamentalMatrices | None,
    lookback: int = 1,
    vol_window: int = 63,
) -> tuple[np.ndarray, dict[str, Any]]:
    score = trailing_return(panel, lookback, 0)
    beta = rolling_beta(panel, 126)
    vol = trailing_vol(panel, vol_window)
    raw = np.full_like(score, np.nan)
    fitted_days = 0
    for i in range(max(126, vol_window, lookback), panel.n_dates):
        mask = panel.member[i] & np.isfinite(score[i]) & np.isfinite(beta[i]) & np.isfinite(vol[i]) & (vol[i] > 0)
        cols = np.flatnonzero(mask)
        if len(cols) < 10:
            continue
        loadings = [np.ones(len(cols)), beta[i, cols]]
        if fundamentals is not None:
            caps = fundamentals.market_cap[i, cols]
            if np.isfinite(caps).sum() >= 10:
                cap_loading = np.log(np.where(np.isfinite(caps) & (caps > 0), caps, np.nan))
                cap_loading = np.where(np.isfinite(cap_loading), cap_loading, np.nanmedian(cap_loading))
                loadings.append(cap_loading)
        X = np.column_stack(loadings)
        z = 1.0 / np.square(vol[i, cols])
        sqrt_z = np.sqrt(z)
        coef = np.linalg.lstsq(X * sqrt_z[:, None], score[i, cols] * sqrt_z, rcond=None)[0]
        residual = score[i, cols] - X @ coef
        raw[i, cols] = -z * residual
        fitted_days += 1
    return normalize_cross_section(raw, np.isfinite(raw) & panel.member), {
        "return_sessions": lookback,
        "regression_weights": f"inverse_volatility_squared_{vol_window}",
        "loadings": ["intercept", "rolling_beta_126", "log_market_cap_when_available"],
        "fitted_days": fitted_days,
    }


def baseline_weighted_regression(
    campaign_id: str,
    panels: dict[str, Panel],
    fundamental: dict[str, FundamentalMatrices],
) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        weights, report = weighted_regression_weights(panels[name], fundamental[name])
        out.append(Variant(
            campaign_id, f"{name}__weighted_regression__return1",
            panels[name], weights, "open_to_close", 1,
            {"short_rule": "intraday_signal_diagnostic", **report},
        ))
    return out


def moving_average(panel: Panel, window: int) -> np.ndarray:
    return pd.DataFrame(panel.adj_close, index=panel.dates).rolling(window, min_periods=window).mean().to_numpy(float)


def baseline_single_ma(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        mask = eligible(panel)
        for window in (20, 50, 100, 200):
            ma = moving_average(panel, window)
            for mode in ("long", "long_short"):
                long_condition = panel.adj_close > ma
                short_condition = panel.adj_close < ma if mode == "long_short" else None
                weights = direct_rule_weights(panel.adj_close, mask & np.isfinite(ma), long_condition=long_condition, short_condition=short_condition)
                out.append(Variant(
                    campaign_id, f"{panel.name}__sma{window}__{mode}", panel, weights,
                    "open_to_next_open", 1, {"moving_average": "SMA", "window": window, "mode": mode},
                ))
    return out


def baseline_two_ma(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        mask = eligible(panel)
        fast = moving_average(panel, 10)
        slow = moving_average(panel, 30)
        long_condition = fast > slow
        short_condition = fast < slow
        for mode in ("long", "long_short"):
            weights = direct_rule_weights(
                fast, mask & np.isfinite(fast) & np.isfinite(slow),
                long_condition=long_condition,
                short_condition=short_condition if mode == "long_short" else None,
            )
            out.append(Variant(
                campaign_id, f"{panel.name}__sma10_30__{mode}", panel, weights,
                "open_to_next_open", 1,
                {"fast": 10, "slow": 30, "paper_stop_2pct": "evaluated_as_separate_adaptation", "mode": mode},
            ))
    return out


def baseline_three_ma(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        mask = eligible(panel)
        ma3 = moving_average(panel, 3)
        ma10 = moving_average(panel, 10)
        ma21 = moving_average(panel, 21)
        long_condition = (ma3 > ma10) & (ma10 > ma21)
        short_condition = (ma3 < ma10) & (ma10 < ma21)
        for mode in ("long", "long_short"):
            state = np.zeros_like(ma3)
            current = np.zeros(panel.n_symbols, dtype=float)
            valid = mask & np.isfinite(ma3) & np.isfinite(ma10) & np.isfinite(ma21)
            for i in range(panel.n_dates):
                current[~valid[i]] = 0.0
                exit_long = (current > 0) & (ma3[i] <= ma10[i])
                exit_short = (current < 0) & (ma3[i] >= ma10[i])
                current[exit_long | exit_short] = 0.0
                current[(current == 0) & valid[i] & long_condition[i]] = 1.0
                if mode == "long_short":
                    current[(current == 0) & valid[i] & short_condition[i]] = -1.0
                gross = np.abs(current).sum()
                if gross > 0:
                    state[i] = current / gross
            weights = state
            out.append(Variant(
                campaign_id, f"{panel.name}__sma3_10_21__{mode}", panel, weights,
                "open_to_next_open", 1,
                {
                    "windows": [3, 10, 21],
                    "entry": "MA3>MA10>MA21 (reverse for short)",
                    "exit": "MA3 crosses MA10 (reverse for short)",
                    "mode": mode,
                },
            ))
    return out


def pivot_weights(panel: Panel, mode: str) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    previous_high = np.vstack([np.full(panel.n_symbols, np.nan), panel.raw_high[:-1]])
    previous_low = np.vstack([np.full(panel.n_symbols, np.nan), panel.raw_low[:-1]])
    previous_close = np.vstack([np.full(panel.n_symbols, np.nan), panel.raw_close[:-1]])
    pivot = (previous_high + previous_low + previous_close) / 3.0
    valid = eligible(panel) & np.isfinite(pivot) & np.isfinite(panel.raw_open)
    long_condition = panel.raw_open > pivot
    short_condition = panel.raw_open < pivot if mode == "long_short" else None
    weights = direct_rule_weights(panel.raw_open, valid, long_condition=long_condition, short_condition=short_condition)
    resistance = 2.0 * pivot - previous_low
    support = 2.0 * pivot - previous_high
    realized = np.full_like(panel.raw_open, np.nan, dtype=float)
    long = weights > 0
    short = weights < 0
    long_target = long & np.isfinite(resistance) & (resistance > panel.raw_open) & (panel.raw_high >= resistance)
    short_target = short & np.isfinite(support) & (support < panel.raw_open) & (panel.raw_low <= support)
    long_exit = np.where(long_target, resistance, panel.raw_close)
    short_exit = np.where(short_target, support, panel.raw_close)
    realized[long] = long_exit[long] / panel.raw_open[long] - 1.0
    realized[short] = short_exit[short] / panel.raw_open[short] - 1.0
    return weights, realized, {
        "long_entries": int(long.sum()),
        "short_entries": int(short.sum()),
        "long_target_hits": int(long_target.sum()),
        "short_target_hits": int(short_target.sum()),
    }


def baseline_pivot(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        for mode in ("long", "long_short"):
            weights, realized, counts = pivot_weights(panel, mode)
            out.append(Variant(
                campaign_id, f"{panel.name}__prior_pivot__target_exit__{mode}", panel,
                weights, "return_override", 0,
                {
                    "entry": "same-day open relative to prior-day pivot C",
                    "target_levels": "R=2C-L and S=2C-H",
                    "unreached_target_exit": "regular-session close",
                    "bar_assumption": "target credited only when it lies beyond entry and daily high/low reaches it",
                    "short_rule": "intraday forced-close signal diagnostic; no protective stop, so not executable",
                    "mode": mode,
                    **counts,
                },
                realized,
            ))
    return out


def channel_weights(panel: Panel, window: int = 20, mode: str = "long_short") -> np.ndarray:
    close = pd.DataFrame(panel.adj_close, index=panel.dates)
    upper = close.shift(1).rolling(window, min_periods=window).max().to_numpy(float)
    lower = close.shift(1).rolling(window, min_periods=window).min().to_numpy(float)
    valid = eligible(panel) & np.isfinite(upper) & np.isfinite(lower)
    touch_low = panel.adj_close <= lower
    touch_high = panel.adj_close >= upper
    state = np.zeros_like(panel.adj_close)
    current = np.zeros(panel.n_symbols, dtype=float)
    for i in range(panel.n_dates):
        current[~valid[i]] = 0.0
        current[valid[i] & touch_low[i]] = 1.0
        current[valid[i] & touch_high[i]] = -1.0 if mode == "long_short" else 0.0
        gross = np.abs(current).sum()
        if gross > 0:
            state[i] = current / gross
    return state


def baseline_channel(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for panel in panels.values():
        for mode in ("long", "long_short"):
            out.append(Variant(
                campaign_id, f"{panel.name}__donchian_close20__{mode}",
                panel, channel_weights(panel, 20, mode), "open_to_next_open", 1,
                {
                    "window": 20,
                    "bounds": "prior 20 completed closing prices",
                    "signal": "long at lower boundary; liquidate/short at upper boundary",
                    "mode": mode,
                    "short_rule": "overnight direct-short leg is a non-executable signal diagnostic",
                },
            ))
    return out


def diagonal_optimizer_weights(
    panel: Panel,
    expected: np.ndarray,
    *,
    dollar_neutral: bool,
    signal_indices: np.ndarray,
    vol_window: int = 60,
) -> np.ndarray:
    vol = trailing_vol(panel, vol_window) / np.sqrt(252.0)
    raw = np.zeros_like(expected)
    for i in signal_indices:
        cols = np.flatnonzero(eligible(panel)[i] & np.isfinite(expected[i]) & np.isfinite(vol[i]) & (vol[i] > 0))
        if len(cols) < 2:
            continue
        inv_var = 1.0 / np.square(vol[i, cols])
        candidate = inv_var * expected[i, cols]
        if dollar_neutral:
            candidate = candidate - inv_var * (candidate.sum() / inv_var.sum())
        scale = float(np.abs(candidate).sum())
        if scale > 0:
            raw[i, cols] = candidate / scale
    return forward_fill_signal_weights(raw, signal_indices)


def baseline_optimizer(campaign_id: str, panels: dict[str, Panel], dollar_neutral: bool) -> list[Variant]:
    out = []
    for panel in panels.values():
        signals = month_end_indices(panel.dates)
        expected = trailing_return(panel, 20, 0)
        weights = diagonal_optimizer_weights(panel, expected, dollar_neutral=dollar_neutral, signal_indices=signals)
        suffix = "dollar_neutral" if dollar_neutral else "unconstrained"
        out.append(Variant(
            campaign_id, f"{panel.name}__momentum20__diag_cov60__{suffix}",
            panel, weights, "open_to_next_open", 1,
            {
                "expected_return": "completed_20_session_return",
                "covariance": "positive_definite_diagonal_60_session_model",
                "normalization": "sum_absolute_weights_one",
                "dollar_neutral": dollar_neutral,
                "short_rule": "overnight short weights are non-executable signal diagnostics",
            },
        ))
    return out


def alpha_holdings(panel: Panel) -> tuple[list[np.ndarray], list[str]]:
    mask = eligible(panel)
    holdings = []
    names = []
    for lookback in (1, 2, 5, 10, 20, 60):
        score = trailing_return(panel, lookback, 0)
        for direction in (1.0, -1.0):
            raw = direction * (score - np.nanmean(score, axis=1, keepdims=True))
            holdings.append(normalize_cross_section(raw, mask & np.isfinite(raw)))
            names.append(f"return{lookback}_{'momentum' if direction > 0 else 'reversal'}")
    for window in (5, 10, 20, 50, 100, 200):
        ma = moving_average(panel, window)
        distance = panel.adj_close / ma - 1.0
        for direction in (1.0, -1.0):
            raw = direction * (distance - np.nanmean(distance, axis=1, keepdims=True))
            holdings.append(normalize_cross_section(raw, mask & np.isfinite(raw)))
            names.append(f"sma{window}_{'trend' if direction > 0 else 'fade'}")
    vol = trailing_vol(panel, 20)
    for direction in (1.0, -1.0):
        raw = direction * (vol - np.nanmean(vol, axis=1, keepdims=True))
        holdings.append(normalize_cross_section(raw, mask & np.isfinite(raw)))
        names.append(f"vol20_{'high' if direction > 0 else 'low'}")
    return holdings, names


def alpha_combo_weights(panel: Panel, history: int = 20, expected_days: int = 5) -> tuple[np.ndarray, dict[str, Any]]:
    holdings, names = alpha_holdings(panel)
    n_alpha = len(holdings)
    asset_ret = np.nan_to_num(panel.open_to_next_open_return, nan=0.0)
    alpha_returns = np.column_stack([np.sum(h * asset_ret, axis=1) for h in holdings])
    combo = np.zeros(panel.adj_close.shape)
    signals = weekly_indices(panel.dates)
    solved = 0
    for i in signals:
        if i < history + expected_days + 1:
            continue
        R = alpha_returns[i - history - 1 : i]
        if R.shape[0] != history + 1:
            continue
        X = R - R.mean(axis=0, keepdims=True)
        sigma = X.std(axis=0, ddof=1)
        valid = np.isfinite(sigma) & (sigma > 1e-10)
        if valid.sum() <= history:
            continue
        Y = X[:, valid] / sigma[valid]
        Y = Y[:-1]
        loadings = Y - Y.mean(axis=1, keepdims=True)
        loadings = loadings[:-1].T
        expected = alpha_returns[i - expected_days : i, valid].mean(axis=0) / sigma[valid]
        residual = expected - loadings @ np.linalg.lstsq(loadings, expected, rcond=None)[0]
        alpha_weight = residual / sigma[valid]
        scale = np.abs(alpha_weight).sum()
        if scale <= 0:
            continue
        alpha_weight /= scale
        valid_indices = np.flatnonzero(valid)
        stock_weight = np.zeros(panel.n_symbols)
        for coeff, alpha_idx in zip(alpha_weight, valid_indices):
            stock_weight += coeff * holdings[int(alpha_idx)][i]
        stock_scale = np.abs(stock_weight).sum()
        if stock_scale > 0:
            combo[i] = stock_weight / stock_scale
            solved += 1
    return forward_fill_signal_weights(combo, signals), {
        "alphas": n_alpha,
        "alpha_names": names,
        "history_days_M": history,
        "expected_return_days": expected_days,
        "rebalance": "weekly",
        "solved_rebalances": solved,
        "short_rule": "overnight short weights are non-executable signal diagnostics",
    }


def baseline_alpha_combo(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    out = []
    for name in ("qqq", "etf"):
        weights, report = alpha_combo_weights(panels[name])
        out.append(Variant(
            campaign_id, f"{name}__26alpha_combo__M20__E5",
            panels[name], weights, "open_to_next_open", 1, report,
        ))
    return out


def sector_panel_mask(panel: Panel) -> np.ndarray:
    mask = np.zeros_like(panel.member, dtype=bool)
    for symbol in SECTOR_ETFS:
        col = panel.symbol_to_col.get(symbol)
        if col is not None:
            mask[:, col] = panel.member[:, col]
    return mask


def baseline_sector_momentum(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    panel = panels["etf"]
    mask = sector_panel_mask(panel)
    signals = month_end_indices(panel.dates)
    out = []
    for formation, hold_months in ((126, 1), (252, 3)):
        score = trailing_return(panel, formation, 0)
        for mode in ("long", "long_short"):
            base = rank_weights(score, mask, signals, mode=mode, top_k=1)
            weights = overlap_monthly_weights(base, signals, hold_months)
            out.append(Variant(
                campaign_id, f"etf_sector__formation{formation}__hold{hold_months}__{mode}",
                panel, weights, "open_to_next_open", 1,
                {
                    "sector_etfs": [s for s in SECTOR_ETFS if s in panel.symbol_to_col],
                    "missing_standard_sector_etfs": ["XLB", "XLC", "XLRE"],
                    "formation_sessions": formation,
                    "holding_months": hold_months,
                    "mode": mode,
                },
            ))
    return out


def filtered_rank_weights(
    score: np.ndarray,
    mask: np.ndarray,
    gate_long: np.ndarray,
    gate_short: np.ndarray,
    signals: np.ndarray,
    mode: str,
) -> np.ndarray:
    base = rank_weights(score, mask, signals, mode=mode, top_k=1)
    signal_only = np.zeros_like(base)
    for i in signals:
        w = base[i].copy()
        w[(w > 0) & ~gate_long[i]] = 0.0
        w[(w < 0) & ~gate_short[i]] = 0.0
        gross = np.abs(w).sum()
        if gross > 0:
            if mode == "long":
                w /= gross
            else:
                positive = w > 0
                negative = w < 0
                if positive.any() and negative.any():
                    w[positive] = w[positive] / w[positive].sum() * 0.5
                    w[negative] = w[negative] / abs(w[negative].sum()) * 0.5
                else:
                    w /= gross
        signal_only[i] = w
    return forward_fill_signal_weights(signal_only, signals)


def baseline_sector_ma(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    panel = panels["etf"]
    mask = sector_panel_mask(panel)
    score = trailing_return(panel, 252, 0)
    signals = month_end_indices(panel.dates)
    out = []
    for window in (100, 200):
        ma = moving_average(panel, window)
        for mode in ("long", "long_short"):
            weights = filtered_rank_weights(
                score, mask, panel.adj_close > ma, panel.adj_close < ma, signals, mode
            )
            out.append(Variant(
                campaign_id, f"etf_sector__mom252__sma{window}__{mode}",
                panel, weights, "open_to_next_open", 1,
                {
                    "formation_sessions": 252,
                    "moving_average_sessions": window,
                    "mode": mode,
                    "missing_standard_sector_etfs": ["XLB", "XLC", "XLRE"],
                },
            ))
    return out


def baseline_dual_sector(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    panel = panels["etf"]
    mask = sector_panel_mask(panel)
    signals = month_end_indices(panel.dates)
    score = trailing_return(panel, 252, 0)
    base = rank_weights(score, mask, signals, mode="long", top_k=1)
    out = []
    spy_col = panel.symbol_to_col["SPY"]
    for window in (100, 200):
        ma = moving_average(panel, window)
        for fallback in ("GLD", "TLT"):
            fallback_col = panel.symbol_to_col[fallback]
            signal_only = np.zeros_like(base)
            for i in signals:
                if np.isfinite(ma[i, spy_col]) and panel.adj_close[i, spy_col] > ma[i, spy_col]:
                    signal_only[i] = base[i]
                elif panel.member[i, fallback_col]:
                    signal_only[i, fallback_col] = 1.0
            weights = forward_fill_signal_weights(signal_only, signals)
            out.append(Variant(
                campaign_id, f"etf_sector__mom252__spy_sma{window}__fallback_{fallback}",
                panel, weights, "open_to_next_open", 1,
                {
                    "formation_sessions": 252,
                    "broad_market": "SPY",
                    "moving_average_sessions": window,
                    "fallback": fallback,
                    "missing_standard_sector_etfs": ["XLB", "XLC", "XLRE"],
                },
            ))
    return out


def baseline_etf_ibs(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    panel = panels["etf"]
    denominator = panel.adj_high - panel.adj_low
    score = np.where(denominator > 0, (panel.adj_close - panel.adj_low) / denominator, np.nan)
    signals = np.arange(panel.n_dates)
    out = []
    for mode in ("reversal_long", "reversal_long_short"):
        weights = rank_weights(score, eligible(panel), signals, mode=mode, quantile=0.10)
        out.append(Variant(
            campaign_id, f"etf_all__ibs__hold1__{mode}", panel, weights,
            "open_to_close", 1,
            {"holding_sessions": 1, "mode": mode, "score_cells": int(np.isfinite(score).sum())},
        ))
    return out


def volatility_target_weights(
    panel: Panel,
    risky_symbol: str,
    target: float,
    window: int,
    rebalance: str,
) -> np.ndarray:
    risky_col = panel.symbol_to_col[risky_symbol]
    vol = trailing_vol(panel, window)
    signals = weekly_indices(panel.dates) if rebalance == "weekly" else month_end_indices(panel.dates)
    raw = np.zeros_like(panel.adj_close)
    for i in signals:
        sigma = vol[i, risky_col]
        if np.isfinite(sigma) and sigma > 0 and panel.member[i, risky_col]:
            raw[i, risky_col] = min(1.0, target / sigma)
    return forward_fill_signal_weights(raw, signals)


def baseline_vol_target(campaign_id: str, panels: dict[str, Panel]) -> list[Variant]:
    panel = panels["etf"]
    out = []
    for risky in ("SPY", "QQQ"):
        for target in (0.10, 0.15):
            for rebalance in ("weekly", "monthly"):
                weights = volatility_target_weights(panel, risky, target, 252, rebalance)
                out.append(Variant(
                    campaign_id, f"etf__{risky}__target{int(target*100)}__vol252__{rebalance}",
                    panel, weights, "open_to_next_open", 1,
                    {
                        "risky_asset": risky,
                        "risk_free_asset": "cash_zero_return_conservative_proxy",
                        "target_volatility": target,
                        "forecast": "trailing_252_session_realized",
                        "rebalance": rebalance,
                        "maximum_risky_weight": 1.0,
                        "margin": "none",
                    },
                ))
    return out


def baseline_distress(
    campaign_id: str,
    panels: dict[str, Panel],
    fundamental: dict[str, FundamentalMatrices],
) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        panel = panels[name]
        score = fundamental[name].chs_logit
        signals = month_end_indices(panel.dates)
        for mode in ("reversal_long", "reversal_long_short"):
            weights = rank_weights(score, eligible(panel) & np.isfinite(score), signals, mode=mode, quantile=0.10)
            out.append(Variant(
                campaign_id, f"{name}__chs_annual_proxy__{mode}",
                panel, weights, "open_to_next_open", 1,
                {
                    "mode": mode,
                    "score": "published_CHS_12month_logit_coefficients_with_annual_accounting_proxy",
                    "coverage": fundamental[name].coverage,
                    "short_rule": "overnight short weights are non-executable signal diagnostics",
                },
            ))
    return out


def scale_sleeve_to_vol(
    panel: Panel,
    weights: np.ndarray,
    target: float,
    window: int,
    signals: np.ndarray,
) -> np.ndarray:
    executed = np.zeros_like(weights)
    executed[1:] = weights[:-1]
    returns = np.sum(executed * np.nan_to_num(panel.open_to_next_open_return, nan=0.0), axis=1)
    vol = pd.Series(returns, index=panel.dates).rolling(window, min_periods=window).std(ddof=1) * np.sqrt(252.0)
    raw = np.zeros_like(weights)
    for i in signals:
        sigma = float(vol.iloc[i]) if np.isfinite(vol.iloc[i]) else np.nan
        if np.isfinite(sigma) and sigma > 0:
            raw[i] = weights[i] * min(1.0, target / sigma)
    return forward_fill_signal_weights(raw, signals)


def baseline_distress_risk(
    campaign_id: str,
    panels: dict[str, Panel],
    fundamental: dict[str, FundamentalMatrices],
) -> list[Variant]:
    out = []
    for name in ("sp500", "qqq"):
        panel = panels[name]
        score = fundamental[name].chs_logit
        signals = month_end_indices(panel.dates)
        for mode in ("reversal_long", "reversal_long_short"):
            base = rank_weights(score, eligible(panel) & np.isfinite(score), signals, mode=mode, quantile=0.10)
            for target in (0.10, 0.15):
                weights = scale_sleeve_to_vol(panel, base, target, 252, signals)
                out.append(Variant(
                    campaign_id, f"{name}__chs_proxy__{mode}__target{int(target*100)}",
                    panel, weights, "open_to_next_open", 1,
                    {
                        "mode": mode,
                        "target_volatility": target,
                        "realized_volatility_window": 252,
                        "maximum_scale": 1.0,
                        "margin": "none",
                        "coverage": fundamental[name].coverage,
                        "short_rule": "overnight short weights are non-executable signal diagnostics",
                    },
                ))
    return out


def build_fundamentals_for_panels(
    panels: dict[str, Panel],
) -> tuple[dict[str, FundamentalMatrices], dict[str, Any]]:
    store = FundamentalStore()
    matrices = {}
    coverage = {}
    for name in ("sp500", "qqq"):
        matrices[name] = build_fundamental_matrices(panels[name], store)
        coverage[name] = matrices[name].coverage
    return matrices, coverage


def build_baselines(
    campaign_id: str,
    panels: dict[str, Panel],
    fundamental: dict[str, FundamentalMatrices],
) -> list[Variant]:
    dispatch = {
        "CAM-0600": lambda: baseline_price_momentum(campaign_id, panels),
        "CAM-0601": lambda: baseline_earnings_momentum(campaign_id, panels),
        "CAM-0602": lambda: baseline_value(campaign_id, panels, fundamental),
        "CAM-0603": lambda: baseline_low_vol(campaign_id, panels),
        "CAM-0604": lambda: baseline_multifactor(campaign_id, panels, fundamental),
        "CAM-0605": lambda: baseline_residual_momentum(campaign_id, panels, fundamental),
        "CAM-0606": lambda: baseline_pairs(campaign_id, panels),
        "CAM-0607": lambda: baseline_single_cluster(campaign_id, panels),
        "CAM-0608": lambda: baseline_multiple_cluster(campaign_id, panels),
        "CAM-0609": lambda: baseline_weighted_regression(campaign_id, panels, fundamental),
        "CAM-0610": lambda: baseline_single_ma(campaign_id, panels),
        "CAM-0611": lambda: baseline_two_ma(campaign_id, panels),
        "CAM-0612": lambda: baseline_three_ma(campaign_id, panels),
        "CAM-0613": lambda: baseline_pivot(campaign_id, panels),
        "CAM-0614": lambda: baseline_channel(campaign_id, panels),
        "CAM-0615": lambda: baseline_optimizer(campaign_id, panels, False),
        "CAM-0616": lambda: baseline_optimizer(campaign_id, panels, True),
        "CAM-0617": lambda: baseline_alpha_combo(campaign_id, panels),
        "CAM-0618": lambda: baseline_sector_momentum(campaign_id, panels),
        "CAM-0619": lambda: baseline_sector_ma(campaign_id, panels),
        "CAM-0620": lambda: baseline_dual_sector(campaign_id, panels),
        "CAM-0621": lambda: baseline_etf_ibs(campaign_id, panels),
        "CAM-0622": lambda: baseline_vol_target(campaign_id, panels),
        "CAM-0623": lambda: baseline_distress(campaign_id, panels, fundamental),
        "CAM-0624": lambda: baseline_distress_risk(campaign_id, panels, fundamental),
    }
    if campaign_id not in dispatch:
        raise KeyError(campaign_id)
    variants = dispatch[campaign_id]()
    if not variants:
        raise RuntimeError(f"{campaign_id} produced zero baseline variants")
    return variants
