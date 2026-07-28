"""Broad, causal daily search for simple recent-regime long-only strategies.

The search uses only fixed, unlevered, liquid ETFs to avoid current-constituent
survivorship and raw-stock split artifacts. Candidate selection ends on
2025-12-31. Only frozen finalists are evaluated on the 2026 holdout.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import duckdb


ETF_UNIVERSE = (
    "SPY", "QQQ", "IWM", "DIA", "SMH", "XLK", "XLF", "XLE", "XLI",
    "XLP", "XLU", "XLV", "XLY", "XLC", "GLD", "TLT", "HYG", "LQD",
    "USO", "ARKK",
)
COST_BPS_SIDE = 5.0
HOLDOUT_START = np.datetime64("2026-01-01")


@dataclass
class Candidate:
    family: str
    spec: str
    gross: np.ndarray
    turnover: np.ndarray
    weights: np.ndarray


def load_bars(glob_path: str) -> tuple[dict[str, pd.DataFrame], list[str]]:
    con = duckdb.connect()
    symbols = ",".join(f"'{s}'" for s in ETF_UNIVERSE)
    query = f"""
    WITH ranked AS (
      SELECT symbol, date, open, high, low, close, volume,
             row_number() OVER (
               PARTITION BY symbol, date
               ORDER BY try_cast(ingested_at AS TIMESTAMPTZ) DESC,
                        source_ingestion_id DESC
             ) AS rn
      FROM read_parquet(?, hive_partitioning=true)
      WHERE symbol IN ({symbols})
        AND date >= DATE '2019-06-21'
        AND adjustment = 'raw'
        AND open > 0 AND high > 0 AND low > 0 AND close > 0
    )
    SELECT symbol, date, open, high, low, close, volume
    FROM ranked WHERE rn=1 ORDER BY date, symbol
    """
    raw = con.execute(query, [glob_path]).fetchdf()
    raw["date"] = pd.to_datetime(raw["date"])
    out = {}
    for field in ("open", "high", "low", "close", "volume"):
        out[field] = raw.pivot(index="date", columns="symbol", values=field).sort_index()
    common = out["close"].columns[out["close"].notna().sum().ge(1500)]
    for field in out:
        out[field] = out[field][common]
    overnight = out["open"] / out["close"].shift(1) - 1
    excluded = list(overnight.columns[overnight.abs().max().gt(0.35)])
    keep = [x for x in common if x not in excluded]
    for field in out:
        out[field] = out[field][keep]
    return out, excluded


def max_drawdown(r: np.ndarray) -> tuple[float, int]:
    eq = np.cumprod(1 + np.nan_to_num(r))
    prior = np.r_[1.0, eq[:-1]]
    peak = np.maximum.accumulate(prior)
    underwater = eq / peak - 1
    dd = float(np.min(underwater)) if len(underwater) else 0.0
    duration = 0
    longest = 0
    for x in underwater:
        duration = duration + 1 if x < -1e-12 else 0
        longest = max(longest, duration)
    return dd, longest


def metrics(dates: np.ndarray, r: np.ndarray) -> dict:
    r = np.nan_to_num(np.asarray(r, dtype=np.float64))
    if len(r) == 0:
        return {
            "total_return": np.nan, "cagr": np.nan, "sharpe": np.nan,
            "max_drawdown": np.nan, "recovery_trading_days": np.nan,
            "worst_month": np.nan, "positive_month_fraction": np.nan,
        }
    total = float(np.prod(1 + r) - 1)
    years = max(len(r) / 252, 1 / 252)
    cagr = float((1 + total) ** (1 / years) - 1) if total > -1 else -1.0
    std = float(np.std(r, ddof=1))
    sharpe = float(np.mean(r) / std * np.sqrt(252)) if std > 0 else 0.0
    dd, duration = max_drawdown(r)
    date_index = pd.to_datetime(dates)
    monthly = (
        pd.Series(r, index=date_index)
        .groupby(date_index.to_period("M"))
        .apply(lambda x: float(np.prod(1 + x) - 1))
    )
    return {
        "total_return": total, "cagr": cagr, "sharpe": sharpe,
        "max_drawdown": dd, "recovery_trading_days": int(duration),
        "worst_month": float(monthly.min()),
        "positive_month_fraction": float((monthly > 0).mean()),
    }


def portfolio_returns(weights: np.ndarray, asset_oo: np.ndarray, cost_bps: float) -> tuple[np.ndarray, np.ndarray]:
    turnover = np.abs(weights - np.vstack([np.zeros(weights.shape[1]), weights[:-1]])).sum(axis=1)
    gross = np.nansum(weights * asset_oo, axis=1)
    net = gross - turnover * cost_bps / 10000
    return net, turnover


def periodic_weights(score: np.ndarray, eligible: np.ndarray, top_n: int, rebalance: int) -> np.ndarray:
    n, m = score.shape
    w = np.zeros((n, m), dtype=np.float32)
    current = np.zeros(m, dtype=np.float32)
    for i in range(n):
        if i % rebalance == 0:
            valid = np.flatnonzero(np.isfinite(score[i]) & eligible[i])
            current = np.zeros(m, dtype=np.float32)
            if len(valid):
                chosen = valid[np.argsort(score[i, valid])[-top_n:]]
                current[chosen] = 1.0 / len(chosen)
        w[i] = current
    return w


def event_hold_weights(events: np.ndarray, hold: int) -> np.ndarray:
    n, m = events.shape
    w = np.zeros((n, m), dtype=np.float32)
    remaining = np.zeros(m, dtype=np.int16)
    for i in range(n):
        remaining = np.maximum(remaining - 1, 0)
        remaining[events[i]] = hold
        active = remaining > 0
        if active.any():
            w[i, active] = 1.0 / active.sum()
    return w


def make_candidates(bars: dict[str, pd.DataFrame]) -> tuple[np.ndarray, list[str], list[Candidate]]:
    close = bars["close"].ffill()
    open_ = bars["open"].reindex(close.index).ffill()
    high = bars["high"].reindex(close.index).ffill()
    low = bars["low"].reindex(close.index).ffill()
    dates = close.index.to_numpy(dtype="datetime64[D]")
    symbols = list(close.columns)
    c = close.to_numpy(float)
    o = open_.to_numpy(float)
    h = high.to_numpy(float)
    l = low.to_numpy(float)

    # A signal formed at close t is shifted to the next open. The return row is
    # open t -> open t+1, so signal-derived weights are shifted one row.
    oo = np.vstack([o[1:] / o[:-1] - 1, np.zeros((1, o.shape[1]))])
    ret1 = c / np.vstack([np.full((1, c.shape[1]), np.nan), c[:-1]]) - 1
    candidates: list[Candidate] = []

    def add(family: str, spec: dict, signal_weights: np.ndarray) -> None:
        weights = np.vstack([np.zeros((1, signal_weights.shape[1])), signal_weights[:-1]])
        net, turnover = portfolio_returns(weights, oo, COST_BPS_SIDE)
        candidates.append(Candidate(family, json.dumps(spec, sort_keys=True), net, turnover, weights))

    # Family 1: cross-asset relative-strength rotation.
    for lookback in (5, 10, 20, 60, 120):
        raw_score = c / np.vstack([np.full((lookback, c.shape[1]), np.nan), c[:-lookback]]) - 1
        vol20 = pd.DataFrame(ret1).rolling(20).std().to_numpy()
        for score_name, score in (("return", raw_score), ("return_over_vol", raw_score / np.maximum(vol20, 0.005))):
            for ma_len in (0, 50, 100, 200):
                eligible = np.isfinite(score)
                if ma_len:
                    ma = pd.DataFrame(c).rolling(ma_len).mean().to_numpy()
                    eligible &= c > ma
                for top_n in (1, 2, 3):
                    for rebalance in (1, 5, 10, 20):
                        w = periodic_weights(score, eligible, top_n, rebalance)
                        add("cross_asset_rotation", {
                            "lookback": lookback, "score": score_name, "ma": ma_len,
                            "top_n": top_n, "rebalance_days": rebalance,
                        }, w)

    # Family 2: single-ETF pullbacks inside an established uptrend.
    for ma_len in (50, 100, 200):
        ma = pd.DataFrame(c).rolling(ma_len).mean().to_numpy()
        for pull_len in (1, 2, 5):
            pull = c / np.vstack([np.full((pull_len, c.shape[1]), np.nan), c[:-pull_len]]) - 1
            for threshold in (-0.01, -0.02, -0.03, -0.05):
                events = (c > ma) & (pull <= threshold)
                for hold in (1, 3, 5, 10):
                    w = event_hold_weights(events, hold)
                    add("trend_pullback_basket", {
                        "ma": ma_len, "pull_days": pull_len,
                        "pull_threshold": threshold, "hold_days": hold,
                    }, w)

    # Family 3: volatility-compression breakouts.
    prev_c = np.vstack([np.full((1, c.shape[1]), np.nan), c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c))) / prev_c
    atr20 = pd.DataFrame(tr).rolling(20).mean()
    for compression_q in (0.20, 0.35, 0.50):
        threshold = atr20.rolling(252).quantile(compression_q).to_numpy()
        compressed = atr20.to_numpy() <= threshold
        for breakout in (20, 60, 120):
            prior_high = pd.DataFrame(c).rolling(breakout).max().shift(1).to_numpy()
            events = compressed & (c > prior_high)
            for hold in (3, 5, 10, 20):
                w = event_hold_weights(events, hold)
                add("compression_breakout_basket", {
                    "compression_quantile": compression_q,
                    "breakout_days": breakout, "hold_days": hold,
                }, w)

    # Family 4: broad risk-on breadth gate, holding a simple fixed growth basket.
    growth = [symbols.index(x) for x in ("QQQ", "SMH", "XLK") if x in symbols]
    for breadth_ma in (50, 100, 200):
        ma = pd.DataFrame(c).rolling(breadth_ma).mean().to_numpy()
        breadth = np.nanmean(c > ma, axis=1)
        for threshold in (0.40, 0.50, 0.60, 0.70):
            for hold_mode in ("equal_growth", "strongest_growth"):
                w = np.zeros_like(c, dtype=np.float32)
                for i in range(len(c)):
                    if breadth[i] < threshold or not growth:
                        continue
                    if hold_mode == "equal_growth":
                        w[i, growth] = 1 / len(growth)
                    else:
                        mom = c[i] / c[max(0, i - 20)] - 1
                        j = growth[int(np.nanargmax(mom[growth]))]
                        w[i, j] = 1
                add("breadth_gated_growth", {
                    "breadth_ma": breadth_ma, "breadth_threshold": threshold,
                    "holding": hold_mode,
                }, w)

    # Family 5: shock rebound across ETFs, with and without long-trend filter.
    for shock_days in (1, 2, 5):
        shock = c / np.vstack([np.full((shock_days, c.shape[1]), np.nan), c[:-shock_days]]) - 1
        for threshold in (-0.02, -0.04, -0.06, -0.08):
            for trend_ma in (0, 100, 200):
                eligible = np.ones_like(c, dtype=bool)
                if trend_ma:
                    eligible &= c > pd.DataFrame(c).rolling(trend_ma).mean().to_numpy()
                events = (shock <= threshold) & eligible
                for hold in (1, 3, 5, 10):
                    w = event_hold_weights(events, hold)
                    add("shock_rebound_basket", {
                        "shock_days": shock_days, "shock_threshold": threshold,
                        "trend_ma": trend_ma, "hold_days": hold,
                    }, w)

    return dates, symbols, candidates, oo


def evaluate(dates: np.ndarray, candidates: list[Candidate]) -> tuple[pd.DataFrame, list[int]]:
    periods = {
        "train_2019_2022": (dates < np.datetime64("2023-01-01")),
        "development_2023_2024": ((dates >= np.datetime64("2023-01-01")) & (dates < np.datetime64("2025-01-01"))),
        "recent_2025": ((dates >= np.datetime64("2025-01-01")) & (dates < HOLDOUT_START)),
    }
    rows = []
    for i, candidate in enumerate(candidates):
        base = {"candidate_id": i, "family": candidate.family, "spec": candidate.spec}
        for label, mask in periods.items():
            r = candidate.gross[mask]
            rows.append({
                **base, "period": label, "trading_days": int(mask.sum()),
                "active_days": int((np.abs(r) > 1e-12).sum()),
                "turnover": float(candidate.turnover[mask].sum()),
                **metrics(dates[mask], r),
            })
    result = pd.DataFrame(rows)
    wide = result.pivot(index=["candidate_id", "family", "spec"], columns="period")
    recent = wide.xs("recent_2025", axis=1, level=1)
    dev = wide.xs("development_2023_2024", axis=1, level=1)
    train = wide.xs("train_2019_2022", axis=1, level=1)
    screen = (
        recent.total_return.ge(0.15)
        & recent.max_drawdown.ge(-0.15)
        & recent.worst_month.ge(-0.08)
        & recent.positive_month_fraction.ge(0.58)
        & recent.active_days.ge(20)
        & recent.recovery_trading_days.le(90)
        & dev.max_drawdown.ge(-0.30)
        & train.max_drawdown.ge(-0.40)
    )
    score = (
        2.0 * recent.total_return + recent.sharpe
        + 0.35 * dev.sharpe + recent.max_drawdown
    )
    selection = pd.DataFrame({
        "candidate_id": recent.index.get_level_values("candidate_id"),
        "family": recent.index.get_level_values("family"),
        "spec": recent.index.get_level_values("spec"),
        "preholdout_screen": screen.to_numpy(),
        "selection_score": score.to_numpy(),
        "recent_return": recent.total_return.to_numpy(),
        "recent_cagr": recent.cagr.to_numpy(),
        "recent_sharpe": recent.sharpe.to_numpy(),
        "recent_max_drawdown": recent.max_drawdown.to_numpy(),
        "recent_recovery_days": recent.recovery_trading_days.to_numpy(),
        "recent_worst_month": recent.worst_month.to_numpy(),
        "recent_positive_month_fraction": recent.positive_month_fraction.to_numpy(),
        "recent_active_days": recent.active_days.to_numpy(),
        "development_cagr": dev.cagr.to_numpy(),
        "development_max_drawdown": dev.max_drawdown.to_numpy(),
        "train_cagr": train.cagr.to_numpy(),
        "train_max_drawdown": train.max_drawdown.to_numpy(),
    }).sort_values(["preholdout_screen", "selection_score"], ascending=[False, False])

    finalists: list[int] = []
    pool = selection[selection.preholdout_screen]
    for _, group in pool.groupby("family", sort=False):
        finalists.extend(group.head(3).candidate_id.astype(int).tolist())
    if not finalists:
        for _, group in selection.groupby("family", sort=False):
            finalists.extend(group.head(1).candidate_id.astype(int).tolist())
    return result, selection, finalists


def holdout_audit(dates: np.ndarray, candidates: list[Candidate], finalists: list[int]) -> pd.DataFrame:
    mask = dates >= HOLDOUT_START
    rows = []
    for candidate_id in finalists:
        candidate = candidates[candidate_id]
        r = candidate.gross[mask]
        rows.append({
            "candidate_id": candidate_id, "family": candidate.family,
            "spec": candidate.spec, "period_start": str(dates[mask][0]),
            "period_end": str(dates[mask][-1]),
            "trading_days": int(mask.sum()),
            "active_days": int((np.abs(r) > 1e-12).sum()),
            "turnover": float(candidate.turnover[mask].sum()),
            **metrics(dates[mask], r),
        })
    return pd.DataFrame(rows).sort_values("total_return", ascending=False)


def cost_audit(dates: np.ndarray, candidates: list[Candidate], finalists: list[int]) -> pd.DataFrame:
    rows = []
    for candidate_id in finalists:
        candidate = candidates[candidate_id]
        gross_before_5 = candidate.gross + candidate.turnover * COST_BPS_SIDE / 10000
        for cost in (0.0, 2.0, 5.0, 10.0):
            net = gross_before_5 - candidate.turnover * cost / 10000
            for label, mask in (
                ("recent_2025", (dates >= np.datetime64("2025-01-01")) & (dates < HOLDOUT_START)),
                ("holdout_2026", dates >= HOLDOUT_START),
            ):
                rows.append({
                    "candidate_id": candidate_id, "family": candidate.family,
                    "cost_bps_side": cost, "period": label,
                    **metrics(dates[mask], net[mask]),
                })
    return pd.DataFrame(rows)


def detailed_audit(
    dates: np.ndarray,
    symbols: list[str],
    asset_oo: np.ndarray,
    candidates: list[Candidate],
    finalists: list[int],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ledger_rows = []
    annual_rows = []
    concentration_rows = []
    for candidate_id in finalists:
        candidate = candidates[candidate_id]
        gross_before_cost = np.nansum(candidate.weights * asset_oo, axis=1)
        cost = candidate.turnover * COST_BPS_SIDE / 10000
        for i in np.flatnonzero(dates >= np.datetime64("2025-01-01")):
            active = np.flatnonzero(candidate.weights[i] > 0)
            ledger_rows.append({
                "candidate_id": candidate_id, "date": str(dates[i]),
                "positions": "|".join(symbols[j] for j in active),
                "weights": "|".join(f"{candidate.weights[i, j]:.6f}" for j in active),
                "gross_return": float(gross_before_cost[i]),
                "turnover": float(candidate.turnover[i]),
                "cost": float(cost[i]), "net_return": float(candidate.gross[i]),
            })
        for year in range(2019, 2027):
            mask = dates.astype("datetime64[Y]") == np.datetime64(str(year))
            if mask.any():
                annual_rows.append({
                    "candidate_id": candidate_id, "family": candidate.family,
                    "year": year, "active_days": int((candidate.weights[mask].sum(axis=1) > 0).sum()),
                    **metrics(dates[mask], candidate.gross[mask]),
                })
        focus = dates >= np.datetime64("2025-01-01")
        contribution = np.nansum(candidate.weights[focus] * asset_oo[focus], axis=0)
        exposure = np.nansum(candidate.weights[focus], axis=0)
        total_abs = float(np.abs(contribution).sum())
        for j in np.argsort(np.abs(contribution))[::-1]:
            if exposure[j] <= 0:
                continue
            concentration_rows.append({
                "candidate_id": candidate_id, "symbol": symbols[j],
                "weighted_active_days": float(exposure[j]),
                "arithmetic_gross_contribution": float(contribution[j]),
                "fraction_abs_contribution": float(abs(contribution[j]) / total_abs) if total_abs else 0,
            })
    return pd.DataFrame(ledger_rows), pd.DataFrame(annual_rows), pd.DataFrame(concentration_rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--bars-glob",
        default="D:/AlgoResearch/data/raw/alpaca/market/stocks/bars_1d/**/*.parquet",
    )
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    bars, excluded_jump_symbols = load_bars(args.bars_glob)
    dates, symbols, candidates, asset_oo = make_candidates(bars)
    development, selection, finalists = evaluate(dates, candidates)
    development.to_csv(out / "development_metrics.csv", index=False)
    selection.to_csv(out / "preholdout_selection.csv", index=False)
    (out / "frozen_finalist_ids.json").write_text(json.dumps(finalists, indent=2), encoding="utf-8")

    # This is the only point at which 2026 is summarized, after finalist IDs
    # have been serialized from the pre-2026 selection.
    holdout = holdout_audit(dates, candidates, finalists)
    holdout.to_csv(out / "sealed_2026_audit.csv", index=False)
    costs = cost_audit(dates, candidates, finalists)
    costs.to_csv(out / "finalist_cost_sensitivity.csv", index=False)
    ledger, annual, concentration = detailed_audit(dates, symbols, asset_oo, candidates, finalists)
    ledger.to_csv(out / "finalist_daily_ledger_2025_2026.csv", index=False)
    annual.to_csv(out / "finalist_annual_metrics.csv", index=False)
    concentration.to_csv(out / "finalist_asset_concentration_2025_2026.csv", index=False)

    family = (
        selection.sort_values(["preholdout_screen", "selection_score"], ascending=[False, False])
        .groupby("family", as_index=False).head(3)
    )
    family.to_csv(out / "family_leaders_preholdout.csv", index=False)
    meta = {
        "data_start": str(dates[0]), "data_end": str(dates[-1]),
        "bar_adjustment": "raw with corporate-action discontinuity exclusions",
        "excluded_jump_symbols": excluded_jump_symbols,
        "symbols": symbols, "candidate_count": len(candidates),
        "preholdout_screen_passes": int(selection.preholdout_screen.sum()),
        "frozen_finalist_ids": finalists, "cost_bps_side": COST_BPS_SIDE,
        "holdout_access": True, "holdout_access_count": 1,
        "holdout_start": str(HOLDOUT_START),
        "selection_end": "2025-12-31",
        "execution": "completed-close signal; next-open entry; open-to-open holding return",
        "portfolio": "long-only; no leverage; fixed unlevered ETF universe; cash permitted",
    }
    (out / "run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    best_pre = selection.iloc[0]
    best_ho = holdout.iloc[0]
    report = f"""# Broad recent daily-phenomena search

## Frozen search result

- {len(candidates):,} simple rules across five independent families.
- {int(selection.preholdout_screen.sum())} rules passed the pre-2026 screen.
- All signals use a completed close and enter at the next open. Costs are {COST_BPS_SIDE:.0f} bp per side via actual portfolio turnover.
- Universe: fixed unlevered ETFs only. Portfolio is long-only, no leverage, and may hold cash when no rule is active.

Best pre-holdout rule: `{best_pre.family}` `{best_pre.spec}`. In 2025 it returned {best_pre.recent_return:.1%}, with {best_pre.recent_max_drawdown:.1%} max drawdown, {int(best_pre.recent_recovery_days)} trading-day recovery, and Sharpe {best_pre.recent_sharpe:.2f}.

## One-time 2026 audit

The frozen finalist set was written before the 2026 summary was computed. Best 2026 finalist: `{best_ho.family}` `{best_ho.spec}`. Through {best_ho.period_end}, it returned {best_ho.total_return:.1%}, with {best_ho.max_drawdown:.1%} max drawdown, {int(best_ho.recovery_trading_days)} trading-day recovery, and Sharpe {best_ho.sharpe:.2f}.

These are bar-level daily results, not live approval. Any surviving rule still requires exact trade ledger review, neighbor robustness, and SIP/open-auction execution checks before paper trading.
"""
    (out / "REPORT.md").write_text(report, encoding="utf-8")
    print(selection.head(20).to_string(index=False))
    print("\n2026 frozen finalists")
    print(holdout.to_string(index=False))
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
