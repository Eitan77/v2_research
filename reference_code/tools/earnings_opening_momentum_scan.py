"""Event-conditioned extension of opening information-persistence momentum."""
from __future__ import annotations

import argparse
import itertools
import json
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import torch

import opening_information_persistence_cuda as base


EVENT_END = pd.Timestamp("2025-06-26")
CHECKPOINTS = (15, 30, 45)
FORMULAS = ("open", "gap_open")
TOP_NS = (1, 2)
MIN_OPEN_BPS = (0.0, 25.0, 50.0)
MIN_GAP_BPS = (0.0, 25.0, 50.0)
SURPRISE_FILTERS = ("reported", "positive", "positive_5pct")


def attach_earnings(df: pd.DataFrame, catalog: str) -> pd.DataFrame:
    con = duckdb.connect(catalog, read_only=True)
    try:
        earnings = con.execute(
            """
            select symbol, earnings_datetime, eps_estimate, reported_eps, surprise_pct,
                   source, source_ingestion_id, ingested_at
            from earnings
            where try_cast(earnings_datetime as timestamptz)
                  between '2019-06-20' and '2025-06-27'
            """
        ).fetchdf()
    finally:
        con.close()
    ts = pd.to_datetime(earnings.earnings_datetime, format="mixed", utc=True)
    et = ts.dt.tz_convert("America/New_York")
    earnings["event_ts_utc"] = ts
    earnings["event_et"] = et
    earnings["event_date"] = et.dt.tz_localize(None).dt.normalize()
    earnings["after_close"] = et.dt.hour.ge(16)
    earnings["premarket"] = et.dt.hour.lt(9) | ((et.dt.hour == 9) & et.dt.minute.lt(30))
    earnings = earnings[earnings.after_close | earnings.premarket].copy()

    sessions = np.array(sorted(pd.to_datetime(df.session_date.unique())), dtype="datetime64[D]")
    event_days = earnings.event_date.to_numpy(dtype="datetime64[D]")
    next_idx = np.searchsorted(sessions, event_days, side="right")
    same_idx = np.searchsorted(sessions, event_days, side="left")
    idx = np.where(earnings.after_close.to_numpy(), next_idx, same_idx)
    valid = idx < len(sessions)
    earnings = earnings.loc[valid].copy()
    earnings["session_date"] = pd.to_datetime(sessions[idx[valid]])
    earnings = earnings.sort_values("event_ts_utc").drop_duplicates(["symbol", "session_date"], keep="last")
    joined = df.merge(
        earnings[[
            "symbol", "session_date", "event_ts_utc", "event_et", "eps_estimate",
            "reported_eps", "surprise_pct", "source", "source_ingestion_id", "ingested_at",
        ]],
        on=["symbol", "session_date"],
        how="inner",
    )
    return joined[joined.session_date.le(EVENT_END)].copy()


def event_mask(df: pd.DataFrame, formula: str, surprise_filter: str) -> pd.Series:
    mask = (df.open_ret > 0) & (df.open_rel > 0)
    if formula == "gap_open":
        mask &= (df.gap_ret > 0) & (df.gap_rel > 0)
    if surprise_filter == "positive":
        mask &= df.surprise_pct.gt(0)
    elif surprise_filter == "positive_5pct":
        mask &= df.surprise_pct.ge(5)
    return mask


def select(
    df: pd.DataFrame,
    cp: int,
    formula: str,
    top_n: int,
    min_open_bps: float,
    min_gap_bps: float,
    surprise_filter: str,
    regime: str,
) -> pd.DataFrame:
    q = df[df.cp.eq(cp)]
    mask = (
        event_mask(q, formula, surprise_filter)
        & q.open_bps.ge(min_open_bps)
        & q.gap_ret.mul(10000).ge(min_gap_bps)
    )
    if regime == "qqq_open_positive":
        mask &= q.q_open_ret.gt(0)
    q = q.loc[mask].copy()
    if q.empty:
        return q
    return (
        q.sort_values(["session_date", f"score_{formula}", "symbol"], ascending=[True, False, True])
        .groupby("session_date", observed=True, as_index=False)
        .head(top_n)
    )


def run(df: pd.DataFrame, calendar: np.ndarray, out: Path) -> pd.DataFrame:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")
    tensor = torch.as_tensor(
        df[["score_open", "score_gap_open", "surprise_pct"]].fillna(0).to_numpy(np.float32),
        device="cuda",
    )
    _ = tensor.amax(dim=0)
    torch.cuda.synchronize()
    rows: list[dict] = []
    bases = itertools.product(
        CHECKPOINTS, FORMULAS, TOP_NS, MIN_OPEN_BPS, MIN_GAP_BPS,
        SURPRISE_FILTERS, base.REGIMES,
    )
    for cp, formula, top_n, min_open, min_gap, surprise_filter, regime in bases:
        picks = select(df, cp, formula, top_n, min_open, min_gap, surprise_filter, regime)
        if picks.empty:
            continue
        for exit_min in base.EXIT_END_MINUTES:
            z = picks.dropna(subset=[f"ret_{exit_min}"])
            if z.empty:
                continue
            gross = z.groupby("session_date", observed=True)[f"ret_{exit_min}"].mean()
            for cost in base.COSTS_BPS_SIDE:
                daily = pd.Series(0.0, index=pd.to_datetime(calendar))
                daily.loc[pd.to_datetime(gross.index)] = gross.values - 2 * cost / 10000
                train = daily.index <= base.TRAIN_END
                valid = daily.index >= base.VALID_START
                row = {
                    "checkpoint_min": cp, "formula": formula, "top_n": top_n,
                    "min_open_bps": min_open, "min_gap_bps": min_gap,
                    "surprise_filter": surprise_filter, "regime": regime,
                    "exit_end_min": exit_min, "cost_bps_side": cost,
                    "active_days": len(gross), "trades": len(z),
                    "trades_per_week": len(z) / len(calendar) * 5,
                }
                row.update({f"full_{k}": v for k, v in base.metrics(daily.index.to_numpy(), daily.to_numpy()).items()})
                row.update({f"train_{k}": v for k, v in base.metrics(daily.index[train].to_numpy(), daily[train].to_numpy()).items()})
                row.update({f"valid_{k}": v for k, v in base.metrics(daily.index[valid].to_numpy(), daily[valid].to_numpy()).items()})
                rows.append(row)
    result = pd.DataFrame(rows)
    result["robust_gate"] = (
        result.cost_bps_side.eq(5)
        & result.train_cagr.gt(0)
        & result.valid_cagr.gt(0)
        & result.full_max_drawdown.gt(-0.15)
        & result.full_dd_duration_calendar_days.le(92)
        & result.full_worst_month.gt(-0.08)
        & result.full_positive_years.ge(result.full_years - 1)
        & result.trades_per_week.ge(1)
    )
    result.to_csv(out / "earnings_grid_metrics.csv", index=False)
    ranked = result[result.cost_bps_side.eq(5)].copy()
    ranked["train_score"] = (
        ranked.train_cagr + 0.5 * ranked.train_sharpe
        + ranked.train_max_drawdown + 0.25 * ranked.train_worst_month
    )
    frozen = ranked.sort_values("train_score", ascending=False).head(30)
    frozen.to_csv(out / "earnings_train_selected_top30.csv", index=False)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="D:/AlgoResearch/data/catalog.duckdb")
    ap.add_argument("--event-matrix", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    base_df = pd.read_parquet(args.event_matrix)
    df = attach_earnings(base_df, args.catalog)
    df.to_parquet(out / "earnings_event_matrix.parquet", index=False)
    calendar = np.array(
        sorted(pd.to_datetime(base_df.loc[pd.to_datetime(base_df.session_date).le(EVENT_END), "session_date"].unique())),
        dtype="datetime64[ns]",
    )
    result = run(df, calendar, out)
    frozen = pd.read_csv(out / "earnings_train_selected_top30.csv")
    best = frozen.sort_values("valid_cagr", ascending=False).iloc[0].to_dict()
    meta = {
        "events": int(len(df)),
        "symbols": int(df.symbol.nunique()),
        "event_sessions": int(df.session_date.nunique()),
        "market_sessions": int(len(calendar)),
        "event_data_end": str(EVENT_END.date()),
        "grid_rows": int(len(result)),
        "robust_gate_rows": int(result.robust_gate.sum()),
        "holdout_access": False,
        "earnings_source": "yfinance historical snapshot ingested 2026-06-21",
        "source_is_point_in_time": False,
        "device": torch.cuda.get_device_name(0),
        "gpu_peak_memory_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 3),
        "elapsed_sec": round(time.perf_counter() - t0, 2),
    }
    (out / "run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    report = f"""# Earnings-conditioned opening momentum

**Verdict: {'RESEARCH CANDIDATE' if meta['robust_gate_rows'] else 'NO CANDIDATE'}.**

- {meta['events']:,} point-in-time-universe event/checkpoint rows across {meta['event_sessions']} event sessions and {meta['market_sessions']} market sessions; {meta['grid_rows']:,} portfolio/cost cells.
- {meta['robust_gate_rows']} rows passed the predeclared 5 bp/side gate.
- The 2026 sealed sample was not accessed.
- Important provenance limitation: earnings timestamps/surprises come from a 2026 yfinance historical snapshot, not a point-in-time event archive. Even a good bar result cannot be promoted without an independent PIT event source.

Best training-frozen row by validation CAGR: checkpoint {int(best['checkpoint_min'])}m, `{best['formula']}`, top {int(best['top_n'])}, surprise `{best['surprise_filter']}`, exit {int(best['exit_end_min']//60):02d}:{int(best['exit_end_min']%60):02d} ET. At 5 bp/side: train CAGR {best['train_cagr']:.1%}, validation CAGR {best['valid_cagr']:.1%}, full CAGR {best['full_cagr']:.1%}, max drawdown {best['full_max_drawdown']:.1%}, worst month {best['full_worst_month']:.1%}, recovery {int(best['full_dd_duration_calendar_days'])} calendar days, {best['trades_per_week']:.2f} trades/week.
"""
    (out / "REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
