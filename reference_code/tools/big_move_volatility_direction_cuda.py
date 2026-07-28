"""Volatility-first, long-only intraday study for SOXL/SOXS.

The study first asks whether early observable information predicts a large
remaining move.  Direction is selected causally from SMH/QQQ/semiconductor
leaders, entry is the following 5-minute bar open, and exits use the complete
subsequent bar path with stop-first same-bar ambiguity.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import torch


SYMBOLS = ("SOXL", "SOXS", "SMH", "QQQ", "NVDA", "AMD", "AVGO")
TRADE_SYMBOLS = ("SOXL", "SOXS")
START = "2019-06-21"
END = "2025-12-31"
TRAIN_END = np.datetime64("2022-12-31")
VALID_START = np.datetime64("2023-01-01")
CHECKPOINTS = (10, 15, 30, 45, 60, 90)
DIRECTION_MODELS = ("smh", "qqq_smh", "semis_blend", "leader_vote", "pair_spread")
COSTS = (0.0, 2.0, 5.0, 10.0)
HORIZONS = (15, 30, 60, 120, "close")
TPS = (100.0, 200.0, 300.0, 500.0)
SLS = (50.0, 100.0, 200.0)


def load_bars(catalog: str, cache: Path) -> pd.DataFrame:
    if cache.exists():
        return pd.read_parquet(cache)
    marks = ",".join("?" for _ in SYMBOLS)
    sql = f"""
    select symbol, session_date, cast(bar_start_ts as timestamptz) as bar_start_ts,
           extract(hour from (cast(bar_start_ts as timestamptz) at time zone 'America/New_York')) * 60
             + extract(minute from (cast(bar_start_ts as timestamptz) at time zone 'America/New_York')) as et_min,
           open, high, low, close, volume
    from read_parquet(
      'D:/AlgoResearch/data/research/matrix/timeframe=5m/symbol=*/*.parquet',
      hive_partitioning=true, union_by_name=true
    )
    where symbol in ({marks})
      and session_date between ? and ?
      and bar_complete
    order by session_date, symbol, et_min
    """
    con = duckdb.connect(catalog, read_only=True)
    try:
        con.execute("set threads=16")
        con.execute("set temp_directory='D:/AlgoResearch/work/duck_tmp'")
        df = con.execute(sql, [*SYMBOLS, START, END]).fetchdf()
    finally:
        con.close()
    df = df[df.et_min.between(570, 955)].copy()
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache, index=False)
    return df


def build_arrays(df: pd.DataFrame) -> dict:
    df = df.copy()
    df["session_date"] = pd.to_datetime(df.session_date)
    dates = np.array(sorted(df.loc[df.symbol.eq("QQQ"), "session_date"].unique()), dtype="datetime64[D]")
    date_to_i = {d: i for i, d in enumerate(dates)}
    symbol_to_i = {s: i for i, s in enumerate(SYMBOLS)}
    bars = 78
    shape = (len(SYMBOLS), len(dates), bars)
    arrays = {k: np.full(shape, np.nan, np.float32) for k in ("open", "high", "low", "close", "volume")}
    for row in df.itertuples(index=False):
        day = np.datetime64(pd.Timestamp(row.session_date).date())
        di = date_to_i.get(day)
        si = symbol_to_i.get(row.symbol)
        bi = int((row.et_min - 570) // 5)
        if di is None or si is None or not 0 <= bi < bars:
            continue
        for col in arrays:
            arrays[col][si, di, bi] = float(getattr(row, col))

    session_open = arrays["open"][:, :, 0]
    session_close = np.full((len(SYMBOLS), len(dates)), np.nan, np.float32)
    day_high = np.full_like(session_close, np.nan)
    day_low = np.full_like(session_close, np.nan)
    for si in range(len(SYMBOLS)):
        for di in range(len(dates)):
            valid = np.isfinite(arrays["close"][si, di])
            if valid.any():
                session_close[si, di] = arrays["close"][si, di, np.flatnonzero(valid)[-1]]
                day_high[si, di] = np.nanmax(arrays["high"][si, di])
                day_low[si, di] = np.nanmin(arrays["low"][si, di])
    prev_close = np.roll(session_close, 1, axis=1)
    prev_close[:, 0] = np.nan
    prev_range = np.roll(day_high / day_low - 1, 1, axis=1)
    prev_range[:, 0] = np.nan

    cum_high = np.maximum.accumulate(np.nan_to_num(arrays["high"], nan=-np.inf), axis=2)
    cum_low = np.minimum.accumulate(np.nan_to_num(arrays["low"], nan=np.inf), axis=2)
    cum_volume = np.nancumsum(arrays["volume"], axis=2)
    # Historical same-checkpoint volume baseline, always shifted one session.
    hist_cum_volume = np.full_like(cum_volume, np.nan)
    for si in range(len(SYMBOLS)):
        for bi in range(bars):
            s = pd.Series(cum_volume[si, :, bi])
            hist_cum_volume[si, :, bi] = s.shift(1).rolling(20, min_periods=10).mean().to_numpy(np.float32)
    arrays.update({
        "dates": dates,
        "symbol_to_i": symbol_to_i,
        "session_open": session_open,
        "session_close": session_close,
        "prev_close": prev_close,
        "prev_range": prev_range,
        "cum_high": cum_high,
        "cum_low": cum_low,
        "cum_volume": cum_volume,
        "hist_cum_volume": hist_cum_volume,
    })
    return arrays


def write_move_distribution(a: dict, out: Path) -> None:
    rows = []
    for symbol in TRADE_SYMBOLS:
        si = a["symbol_to_i"][symbol]
        op = a["session_open"][si]
        cl = a["session_close"][si]
        rng = a["cum_high"][si, :, -1] / a["cum_low"][si, :, -1] - 1
        abs_oc = np.abs(cl / op - 1)
        valid = np.isfinite(rng) & np.isfinite(abs_oc)
        for label, vals in (("high_low_range", rng[valid]), ("absolute_open_close", abs_oc[valid])):
            rows.append({
                "symbol": symbol, "measure": label, "sessions": len(vals),
                "mean": float(np.mean(vals)), "median": float(np.median(vals)),
                "p75": float(np.quantile(vals, .75)), "p90": float(np.quantile(vals, .90)),
                "p95": float(np.quantile(vals, .95)), "p99": float(np.quantile(vals, .99)),
                "ge_5pct_fraction": float(np.mean(vals >= .05)),
                "ge_10pct_fraction": float(np.mean(vals >= .10)),
            })
    pd.DataFrame(rows).to_csv(out / "daily_move_distribution.csv", index=False)


def direction_score(a: dict, model: str, signal_i: int) -> np.ndarray:
    idx = a["symbol_to_i"]
    def ret(symbol: str) -> np.ndarray:
        si = idx[symbol]
        return a["close"][si, :, signal_i] / a["session_open"][si] - 1
    smh, qqq = ret("SMH"), ret("QQQ")
    nvda, amd, avgo = ret("NVDA"), ret("AMD"), ret("AVGO")
    soxl, soxs = ret("SOXL"), ret("SOXS")
    if model == "smh":
        return smh
    if model == "qqq_smh":
        return .7 * smh + .3 * qqq
    if model == "semis_blend":
        return .4 * smh + .2 * nvda + .2 * amd + .2 * avgo
    if model == "leader_vote":
        vote = np.sign(nvda) + np.sign(amd) + np.sign(avgo)
        confidence = (np.abs(nvda) + np.abs(amd) + np.abs(avgo)) / 3
        return np.sign(vote) * confidence
    return .5 * (soxl - soxs)


def gate_specs() -> list[dict]:
    specs = [{"gate": "none", "score_bps": 0, "range_bps": 0, "rvol": 0, "prev_range_bps": 0}]
    specs += [{"gate": "score", "score_bps": x, "range_bps": 0, "rvol": 0, "prev_range_bps": 0}
              for x in (10, 25, 50, 100)]
    specs += [{"gate": "early_range", "score_bps": 0, "range_bps": x, "rvol": 0, "prev_range_bps": 0}
              for x in (100, 200, 300, 500)]
    specs += [{"gate": "opening_rvol", "score_bps": 0, "range_bps": 0, "rvol": x, "prev_range_bps": 0}
              for x in (1.0, 1.25, 1.5, 2.0)]
    specs += [{"gate": "prev_range", "score_bps": 0, "range_bps": 0, "rvol": 0, "prev_range_bps": x}
              for x in (300, 500, 750)]
    specs += [
        {"gate": "score_range_rvol", "score_bps": score, "range_bps": rng, "rvol": rv, "prev_range_bps": 0}
        for score, rng, rv in itertools.product((25, 50, 100), (100, 300), (1.0, 1.25))
    ]
    return specs


def exit_specs() -> list[dict]:
    specs = [{"horizon": h, "tp_bps": 0.0, "sl_bps": 0.0} for h in HORIZONS]
    specs += [
        {"horizon": h, "tp_bps": tp, "sl_bps": sl}
        for h, tp, sl in itertools.product((60, 120, "close"), TPS, SLS)
    ]
    return specs


def selected_paths(a: dict, score: np.ndarray, checkpoint: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    entry_i = checkpoint // 5
    positive = score > 0
    idx = a["symbol_to_i"]
    long_i = idx["SOXL"]
    inverse_i = idx["SOXS"]
    op = np.where(positive, a["open"][long_i, :, entry_i], a["open"][inverse_i, :, entry_i])
    hi = np.where(
        positive[:, None],
        a["high"][long_i, :, entry_i:],
        a["high"][inverse_i, :, entry_i:],
    )
    lo = np.where(
        positive[:, None],
        a["low"][long_i, :, entry_i:],
        a["low"][inverse_i, :, entry_i:],
    )
    cl = np.where(
        positive[:, None],
        a["close"][long_i, :, entry_i:],
        a["close"][inverse_i, :, entry_i:],
    )
    return op, hi, lo, cl


def path_returns_gpu(
    entry: np.ndarray, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, specs: list[dict]
) -> np.ndarray:
    device = torch.device("cuda")
    en = torch.tensor(np.array(entry, copy=True), dtype=torch.float32, device=device)
    hi = torch.tensor(np.array(highs, copy=True), dtype=torch.float32, device=device)
    lo = torch.tensor(np.array(lows, copy=True), dtype=torch.float32, device=device)
    cl = torch.tensor(np.array(closes, copy=True), dtype=torch.float32, device=device)
    rows = []
    for spec in specs:
        horizon = spec["horizon"]
        bars = cl.shape[1] if horizon == "close" else min(int(horizon) // 5, cl.shape[1])
        end_close = cl[:, bars - 1]
        tp = float(spec["tp_bps"]) / 10000
        sl = float(spec["sl_bps"]) / 10000
        gross = end_close / en - 1
        if tp > 0 or sl > 0:
            h = hi[:, :bars]
            l = lo[:, :bars]
            sentinel = bars + 1
            if sl > 0:
                stop_hits = l <= en[:, None] * (1 - sl)
                stop_any = stop_hits.any(dim=1)
                stop_first = torch.where(stop_any, stop_hits.float().argmax(dim=1), sentinel)
            else:
                stop_first = torch.full((len(en),), sentinel, device=device)
            if tp > 0:
                take_hits = h >= en[:, None] * (1 + tp)
                take_any = take_hits.any(dim=1)
                take_first = torch.where(take_any, take_hits.float().argmax(dim=1), sentinel)
            else:
                take_first = torch.full((len(en),), sentinel, device=device)
            # Stop wins any same-bar collision.
            gross = torch.where(
                stop_first <= take_first,
                torch.full_like(gross, -sl),
                torch.where(take_first < stop_first, torch.full_like(gross, tp), gross),
            )
        valid = torch.isfinite(en) & torch.isfinite(end_close) & en.gt(0)
        gross = torch.where(valid, gross, torch.nan)
        rows.append(gross)
    torch.cuda.synchronize()
    return torch.stack(rows, dim=1).cpu().numpy()


def drawdown_stats(dates: np.ndarray, returns: np.ndarray) -> tuple[float, int]:
    eq = np.cumprod(1 + returns)
    peaks = np.maximum.accumulate(np.r_[1.0, eq])[:-1]
    dd = eq / peaks - 1
    under = dd < -1e-12
    if not under.any():
        return float(dd.min(initial=0)), 0
    changes = np.diff(np.r_[False, under, False].astype(np.int8))
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1) - 1
    days = dates.astype("datetime64[D]").astype(np.int64)
    return float(dd.min()), int(np.max(days[ends] - days[starts]))


def metrics(dates: np.ndarray, returns: np.ndarray) -> dict:
    returns = np.nan_to_num(returns, nan=0.0)
    dd, dd_days = drawdown_stats(dates, returns)
    eq = float(np.prod(1 + returns))
    day_int = dates.astype("datetime64[D]").astype(np.int64)
    years_span = max((day_int[-1] - day_int[0]) / 365.25, 1 / 252)
    cagr = eq ** (1 / years_span) - 1 if eq > 0 else -1
    std = float(np.std(returns, ddof=1))
    sharpe = float(np.mean(returns) / std * math.sqrt(252)) if std > 0 else 0
    month = dates.astype("datetime64[M]").astype(np.int64)
    _, mi = np.unique(month, return_inverse=True)
    monthly = np.expm1(np.bincount(mi, weights=np.log1p(np.clip(returns, -.999999, None))))
    year = dates.astype("datetime64[Y]").astype(np.int64)
    _, yi = np.unique(year, return_inverse=True)
    yearly = np.expm1(np.bincount(yi, weights=np.log1p(np.clip(returns, -.999999, None))))
    return {
        "cagr": cagr, "sharpe": sharpe, "max_drawdown": dd,
        "dd_duration_calendar_days": dd_days, "worst_month": float(monthly.min()),
        "positive_month_fraction": float((monthly > 0).mean()),
        "positive_years": int((yearly > 0).sum()), "years": len(yearly),
    }


def scan(a: dict, out: Path) -> pd.DataFrame:
    dates = a["dates"]
    train = dates <= TRAIN_END
    valid = dates >= VALID_START
    idx = a["symbol_to_i"]
    gates = gate_specs()
    exits = exit_specs()
    rows = []
    anomaly_rows = []
    top_ledgers = []
    for checkpoint in CHECKPOINTS:
        signal_i = checkpoint // 5 - 1
        pair_range = np.maximum(
            a["cum_high"][idx["SOXL"], :, signal_i] / a["cum_low"][idx["SOXL"], :, signal_i] - 1,
            a["cum_high"][idx["SOXS"], :, signal_i] / a["cum_low"][idx["SOXS"], :, signal_i] - 1,
        )
        pair_rvol = np.nanmean(np.stack([
            a["cum_volume"][idx["SOXL"], :, signal_i] / a["hist_cum_volume"][idx["SOXL"], :, signal_i],
            a["cum_volume"][idx["SOXS"], :, signal_i] / a["hist_cum_volume"][idx["SOXS"], :, signal_i],
        ]), axis=0)
        prev_range = np.nanmax(np.stack([
            a["prev_range"][idx["SOXL"]], a["prev_range"][idx["SOXS"]],
        ]), axis=0)
        for model in DIRECTION_MODELS:
            score = direction_score(a, model, signal_i)
            entry, highs, lows, closes = selected_paths(a, score, checkpoint)
            ret_matrix = path_returns_gpu(entry, highs, lows, closes, exits)
            oracle_mfe = np.nanmax(highs / entry[:, None] - 1, axis=1)
            for threshold in (0, 10, 25, 50, 100, 200):
                mask = np.isfinite(score) & (np.abs(score) * 10000 >= threshold)
                if mask.sum() < 30:
                    continue
                end_ret = ret_matrix[:, 4]
                anomaly_rows.append({
                    "checkpoint_min": checkpoint, "direction_model": model,
                    "min_abs_score_bps": threshold, "events": int(mask.sum()),
                    "mean_close_return": float(np.nanmean(end_ret[mask])),
                    "median_close_return": float(np.nanmedian(end_ret[mask])),
                    "win_rate_close": float(np.nanmean(end_ret[mask] > 0)),
                    "mean_remaining_mfe": float(np.nanmean(oracle_mfe[mask])),
                    "mfe_ge_2pct": float(np.nanmean(oracle_mfe[mask] >= .02)),
                    "mfe_ge_5pct": float(np.nanmean(oracle_mfe[mask] >= .05)),
                })
            for gate_id, gate in enumerate(gates):
                mask = (
                    np.isfinite(score) & (score != 0)
                    & (np.abs(score) * 10000 >= gate["score_bps"])
                    & (pair_range * 10000 >= gate["range_bps"])
                    & (pair_rvol >= gate["rvol"])
                    & (prev_range * 10000 >= gate["prev_range_bps"])
                )
                count = int(mask.sum())
                if count < 60:
                    continue
                for exit_id, exit_spec in enumerate(exits):
                    gross = ret_matrix[:, exit_id]
                    active = mask & np.isfinite(gross)
                    if active.sum() < 60:
                        continue
                    for cost in COSTS:
                        daily = np.zeros(len(dates), np.float64)
                        daily[active] = gross[active] - 2 * cost / 10000
                        base = {
                            "checkpoint_min": checkpoint, "direction_model": model,
                            "gate_id": gate_id, **gate, "exit_id": exit_id, **exit_spec,
                            "cost_bps_side": cost, "trades": int(active.sum()),
                            "trades_per_week": float(active.sum() / len(dates) * 5),
                            "mean_gross_trade": float(np.mean(gross[active])),
                            "capture_2pct_fraction": float(np.mean(gross[active] >= .02)),
                        }
                        row = {**base, **{f"full_{k}": v for k, v in metrics(dates, daily).items()}}
                        row.update({f"train_{k}": v for k, v in metrics(dates[train], daily[train]).items()})
                        row.update({f"valid_{k}": v for k, v in metrics(dates[valid], daily[valid]).items()})
                        rows.append(row)
    result = pd.DataFrame(rows)
    result["robust_gate"] = (
        result.cost_bps_side.eq(5)
        & result.train_cagr.gt(0) & result.valid_cagr.gt(0)
        & result.full_max_drawdown.gt(-.15)
        & result.full_dd_duration_calendar_days.le(92)
        & result.full_worst_month.gt(-.08)
        & result.full_positive_years.ge(result.full_years - 1)
        & result.trades_per_week.ge(1)
    )
    result.to_csv(out / "volatility_direction_grid.csv", index=False)
    pd.DataFrame(anomaly_rows).to_csv(out / "large_move_anomaly_map.csv", index=False)
    return result


def write_report(a: dict, result: pd.DataFrame, out: Path, elapsed: float) -> None:
    cost5 = result[result.cost_bps_side.eq(5)].copy()
    cost5["train_score"] = (
        cost5.train_cagr + .5 * cost5.train_sharpe
        + cost5.train_max_drawdown + .25 * cost5.train_worst_month
    )
    frozen = cost5.sort_values("train_score", ascending=False).head(50)
    frozen.to_csv(out / "training_frozen_top50.csv", index=False)
    best = frozen.sort_values("valid_cagr", ascending=False).iloc[0]
    robust = int(result.robust_gate.sum())
    move = pd.read_csv(out / "daily_move_distribution.csv")
    soxl_range = move[(move.symbol == "SOXL") & (move.measure == "high_low_range")].iloc[0]
    meta = {
        "data_start": str(a["dates"][0]), "data_end": str(a["dates"][-1]),
        "sealed_holdout_start": "2026-01-01", "holdout_access": False,
        "sessions": len(a["dates"]), "grid_rows": len(result), "robust_gate_rows": robust,
        "direction_models": list(DIRECTION_MODELS), "checkpoints": list(CHECKPOINTS),
        "gate_specs": len(gate_specs()), "exit_specs": len(exit_specs()),
        "device": torch.cuda.get_device_name(0),
        "gpu_peak_memory_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 3),
        "elapsed_sec": round(elapsed, 2),
    }
    (out / "run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    verdict = "RESEARCH CANDIDATE" if robust else "NO CANDIDATE"
    text = f"""# Volatility-first SOXL/SOXS big-move study

**Verdict: {verdict}.** The sealed 2026 sample was not accessed.

## Opportunity audit

- SOXL mean daily high-low range: {soxl_range['mean']:.2%}; median {soxl_range['median']:.2%}; 90th percentile {soxl_range['p90']:.2%}.
- Fraction of SOXL sessions with at least a 10% high-low range: {soxl_range['ge_10pct_fraction']:.1%}.
- A large ex-post range is not itself tradable; the strategy grid uses only completed early bars to predict magnitude and direction.

## Search

- {meta['sessions']} sessions, {meta['grid_rows']:,} costed portfolio cells.
- Six causal checkpoints, five directional models, {meta['gate_specs']} volatility gates, and {meta['exit_specs']} timed or full-path bracket exits.
- Next-5-minute-bar-open entry. TP/SL paths use every bar and the stop wins a same-bar collision.
- Training ranking: 2019–2022. Internal validation: 2023–2025. Costs: 0/2/5/10 bp per side.
- CUDA: {meta['device']}; peak allocated {meta['gpu_peak_memory_gb']} GB.

## Best training-frozen row by validation CAGR

- Checkpoint {int(best['checkpoint_min'])}m; direction `{best['direction_model']}`; gate `{best['gate']}`; score ≥{best['score_bps']:.0f} bp, early range ≥{best['range_bps']:.0f} bp, RVOL ≥{best['rvol']:.2f}, previous range ≥{best['prev_range_bps']:.0f} bp.
- Exit horizon `{best['horizon']}`, TP {best['tp_bps']:.0f} bp, SL {best['sl_bps']:.0f} bp.
- At 5 bp/side: train CAGR {best['train_cagr']:.1%}, validation CAGR {best['valid_cagr']:.1%}, full CAGR {best['full_cagr']:.1%}, max drawdown {best['full_max_drawdown']:.1%}, worst month {best['full_worst_month']:.1%}, recovery {int(best['full_dd_duration_calendar_days'])} calendar days, {best['trades_per_week']:.2f} trades/week.

No passive or pullback-limit fill is credited in this stage. Such entry variants are permitted only after a market-entry bar candidate clears the gate, then require conservative fill accounting and SIP quote replay.
"""
    (out / "REPORT.md").write_text(text, encoding="utf-8")
    print(json.dumps(meta, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="D:/AlgoResearch/data/catalog.duckdb")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    bars = load_bars(args.catalog, out / "bars_cache.parquet")
    a = build_arrays(bars)
    write_move_distribution(a, out)
    result = scan(a, out)
    write_report(a, result, out, time.perf_counter() - t0)


if __name__ == "__main__":
    main()
