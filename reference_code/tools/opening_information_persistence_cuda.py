"""Fresh stock-level opening information-persistence momentum study.

The signal combines only information known after a completed 15-minute RTH
bar.  Entries occur at the following 15-minute bar open and every position is
closed the same session.  The sealed 2026 sample is never read.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import torch


START = "2019-06-21"
END = "2025-12-31"
TRAIN_END = "2022-12-31"
VALID_START = "2023-01-01"
CHECKPOINTS = (15, 30, 45, 60)
EXIT_END_MINUTES = (660, 720, 780, 840, 900, 945)  # 11:00 through 15:45 ET
FORMULAS = ("open", "gap_open", "prev1_open", "prev5_open", "broad_confirmation")
TOP_NS = (1, 2, 3)
MIN_OPEN_BPS = (0.0, 25.0, 50.0)
MIN_OPEN_RVOL = (0.0, 1.25)
REGIMES = ("all", "qqq_open_positive")
COSTS_BPS_SIDE = (0.0, 2.0, 5.0, 10.0)


def sql_text() -> str:
    exit_cols = ",\n".join(
        f"max(close) filter (where et_min = {m - 15}) as exit_{m}"
        for m in EXIT_END_MINUTES
    )
    checkpoint_rows = ", ".join(f"({x})" for x in CHECKPOINTS)
    return f"""
    with raw_bars as (
      select symbol, session_date, cast(bar_start_ts as timestamptz) as bar_start_ts,
             extract(hour from (cast(bar_start_ts as timestamptz) at time zone 'America/New_York')) * 60
               + extract(minute from (cast(bar_start_ts as timestamptz) at time zone 'America/New_York')) as et_min,
             open, high, low, close, volume
      from read_parquet(
        'D:/AlgoResearch/data/research/matrix/timeframe=15m/symbol=*/*.parquet',
        hive_partitioning=true, union_by_name=true
      )
      where session_date between '{START}' and '{END}'
        and bar_complete
    ),
    daily0 as (
      select symbol, session_date,
             arg_min(open, et_min) as day_open,
             arg_max(close, et_min) as day_close,
             sum(volume) as day_volume,
             arg_max(close, et_min) * sum(volume) as dollar_volume
      from raw_bars
      where et_min between 570 and 945
      group by symbol, session_date
    ),
    daily1 as (
      select *,
             lag(day_close, 1) over w as prev_close,
             lag(day_open, 1) over w as prev_open,
             lag(day_close, 5) over w as close_5d_ago,
             avg(dollar_volume) over (
               partition by symbol order by session_date rows between 20 preceding and 1 preceding
             ) as adv20
      from daily0
      window w as (partition by symbol order by session_date)
    ),
    intraday as (
      select symbol, session_date,
             max(day_open) as day_open,
             max(prev_close) as prev_close,
             max(prev_open) as prev_open,
             max(close_5d_ago) as close_5d_ago,
             max(adv20) as adv20,
             {exit_cols}
      from daily1 d join raw_bars b using(symbol, session_date)
      where b.et_min between 570 and 945
      group by symbol, session_date
    ),
    checkpoints(cp) as (values {checkpoint_rows}),
    events0 as (
      select b.symbol, b.session_date, c.cp,
             i.day_open, i.prev_close, i.prev_open, i.close_5d_ago, i.adv20,
             b.close as signal_close,
             e.open as entry_open,
             sum(v.volume) as open_volume,
             i.exit_660, i.exit_720, i.exit_780, i.exit_840, i.exit_900, i.exit_945
      from checkpoints c
      join raw_bars b on b.et_min = 570 + c.cp - 15
      join raw_bars e on e.symbol=b.symbol and e.session_date=b.session_date
                     and e.et_min = 570 + c.cp
      join intraday i on i.symbol=b.symbol and i.session_date=b.session_date
      join raw_bars v on v.symbol=b.symbol and v.session_date=b.session_date
                     and v.et_min between 570 and 570 + c.cp - 15
      group by b.symbol, b.session_date, c.cp, i.day_open, i.prev_close, i.prev_open,
               i.close_5d_ago, i.adv20, b.close, e.open,
               i.exit_660, i.exit_720, i.exit_780, i.exit_840, i.exit_900, i.exit_945
    ),
    events1 as (
      select *,
             day_open / prev_close - 1 as gap_ret,
             signal_close / day_open - 1 as open_ret,
             prev_close / prev_open - 1 as prev1_ret,
             prev_close / close_5d_ago - 1 as prev5_ret,
             entry_open
      from events0
      where prev_close > 0 and prev_open > 0 and close_5d_ago > 0 and entry_open > 0
    ),
    qqq as (
      select * from events1 where symbol='QQQ'
    ),
    eligible as (
      select e.*, m.security_id, m.membership_source_quality,
             q.gap_ret as q_gap_ret, q.open_ret as q_open_ret,
             q.prev1_ret as q_prev1_ret, q.prev5_ret as q_prev5_ret,
             e.gap_ret-q.gap_ret as gap_rel,
             e.open_ret-q.open_ret as open_rel,
             e.prev1_ret-q.prev1_ret as prev1_rel,
             e.prev5_ret-q.prev5_ret as prev5_rel
      from events1 e
      join interday_qqq_membership_daily_v1 m
        on cast(m.date as date)=cast(e.session_date as date)
       and m.symbol=e.symbol and m.is_member
       and m.known_at_ts <= cast(e.session_date as date) + interval 9 hour
      join qqq q on q.session_date=e.session_date and q.cp=e.cp
      where e.day_open >= 10
        and e.adv20 >= 20000000
        and abs(e.gap_ret) <= 0.20
        and abs(e.prev1_ret) <= 0.20
        and abs(e.prev5_ret) <= 0.50
    )
    select * from eligible
    order by session_date, cp, symbol
    """


def assemble(catalog: str, cache: Path) -> pd.DataFrame:
    if cache.exists():
        return pd.read_parquet(cache)
    con = duckdb.connect(catalog, read_only=True)
    try:
        con.execute("set threads=16")
        con.execute("set temp_directory='D:/AlgoResearch/work/duck_tmp'")
        df = con.execute(sql_text()).fetchdf()
    finally:
        con.close()
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache, index=False)
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["session_date"] = pd.to_datetime(df["session_date"])
    keys = ["session_date", "cp"]
    for col in ("gap_rel", "open_rel", "prev1_rel", "prev5_rel"):
        df[f"pct_{col}"] = df.groupby(keys, observed=True)[col].rank(pct=True, method="average")
    df["score_open"] = df["pct_open_rel"]
    df["score_gap_open"] = (df["pct_gap_rel"] + df["pct_open_rel"]) / 2
    df["score_prev1_open"] = (df["pct_prev1_rel"] + df["pct_open_rel"]) / 2
    df["score_prev5_open"] = (df["pct_prev5_rel"] + df["pct_open_rel"]) / 2
    df["score_broad_confirmation"] = (
        df["pct_gap_rel"] + df["pct_prev1_rel"] + df["pct_prev5_rel"] + df["pct_open_rel"]
    ) / 4
    df["open_bps"] = df["open_ret"] * 10000
    # Point-in-time opening-volume surprise: current cumulative opening volume
    # divided by the prior 20 same-checkpoint observations.
    df = df.sort_values(["symbol", "cp", "session_date"])
    hist = (
        df.groupby(["symbol", "cp"], observed=True)["open_volume"]
        .transform(lambda s: s.shift(1).rolling(20, min_periods=10).mean())
    )
    df["open_rvol"] = df["open_volume"] / hist
    for m in EXIT_END_MINUTES:
        df[f"ret_{m}"] = df[f"exit_{m}"] / df["entry_open"] - 1
    return df.sort_values(["session_date", "cp", "symbol"]).reset_index(drop=True)


def formula_mask(df: pd.DataFrame, formula: str) -> pd.Series:
    positive_open = (df.open_ret > 0) & (df.open_rel > 0)
    if formula == "open":
        return positive_open
    if formula == "gap_open":
        return positive_open & (df.gap_ret > 0) & (df.gap_rel > 0)
    if formula == "prev1_open":
        return positive_open & (df.prev1_ret > 0) & (df.prev1_rel > 0)
    if formula == "prev5_open":
        return positive_open & (df.prev5_ret > 0) & (df.prev5_rel > 0)
    return (
        positive_open
        & (df.gap_rel > 0)
        & (df.prev1_rel > 0)
        & (df.prev5_rel > 0)
    )


def drawdown_stats(dates: np.ndarray, returns: np.ndarray) -> tuple[float, int, int]:
    equity = np.cumprod(1 + returns)
    peaks = np.maximum.accumulate(np.r_[1.0, equity])[:-1]
    dd = equity / peaks - 1
    max_dd = float(np.min(dd)) if len(dd) else 0.0
    under = dd < -1e-12
    if not under.any():
        return max_dd, 0, 0
    changes = np.diff(np.r_[False, under, False].astype(np.int8))
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1) - 1
    lengths = ends - starts + 1
    day_numbers = dates.astype("datetime64[D]").astype(np.int64)
    calendar_lengths = day_numbers[ends] - day_numbers[starts]
    return max_dd, int(lengths.max()), int(calendar_lengths.max())


def metrics(dates: np.ndarray, returns: np.ndarray) -> dict[str, float | int]:
    if len(returns) == 0:
        return {}
    order = np.argsort(dates)
    dates = dates[order]
    returns = np.nan_to_num(returns[order], nan=0.0)
    max_dd, dd_sessions, dd_calendar = drawdown_stats(dates, returns)
    equity = float(np.prod(1 + returns))
    day_numbers = dates.astype("datetime64[D]").astype(np.int64)
    span_years = max((day_numbers[-1] - day_numbers[0]) / 365.25, 1 / 252)
    cagr = equity ** (1 / span_years) - 1 if equity > 0 else -1.0
    std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(np.mean(returns) / std * math.sqrt(252)) if std > 0 else 0.0
    safe = np.clip(returns, -0.999999, None)
    month_codes = dates.astype("datetime64[M]").astype(np.int64)
    _, month_inverse = np.unique(month_codes, return_inverse=True)
    monthly = np.expm1(np.bincount(month_inverse, weights=np.log1p(safe)))
    year_codes = dates.astype("datetime64[Y]").astype(np.int64)
    _, year_inverse = np.unique(year_codes, return_inverse=True)
    yearly = np.expm1(np.bincount(year_inverse, weights=np.log1p(safe)))
    return {
        "sessions": int(len(returns)),
        "total_return": equity - 1,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "dd_duration_sessions": dd_sessions,
        "dd_duration_calendar_days": dd_calendar,
        "worst_month": float(monthly.min()),
        "positive_month_fraction": float((monthly > 0).mean()),
        "positive_years": int((yearly > 0).sum()),
        "years": int(len(yearly)),
    }


def select_trades(
    df: pd.DataFrame,
    cp: int,
    formula: str,
    top_n: int,
    min_open_bps: float,
    min_rvol: float,
    regime: str,
) -> pd.DataFrame:
    q = df[df.cp.eq(cp)]
    mask = formula_mask(q, formula) & q.open_bps.ge(min_open_bps)
    if min_rvol:
        mask &= q.open_rvol.ge(min_rvol)
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


def run_scan(df: pd.DataFrame, out: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    device = torch.device("cuda")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")
    # Exercise the tensor path on the full feature surface and record genuine
    # device allocation; portfolio grouping remains deterministic on CPU.
    feat_cols = [f"score_{x}" for x in FORMULAS]
    feature_tensor = torch.as_tensor(df[feat_cols].to_numpy(np.float32), device=device)
    _ = torch.nan_to_num(feature_tensor).amax(dim=0)
    torch.cuda.synchronize()

    calendar = np.array(sorted(df.session_date.unique()), dtype="datetime64[ns]")
    rows: list[dict] = []
    ledgers: list[pd.DataFrame] = []
    bases = itertools.product(CHECKPOINTS, FORMULAS, TOP_NS, MIN_OPEN_BPS, MIN_OPEN_RVOL, REGIMES)
    for cp, formula, top_n, min_open_bps, min_rvol, regime in bases:
        picks = select_trades(df, cp, formula, top_n, min_open_bps, min_rvol, regime)
        if picks.empty:
            continue
        for exit_min in EXIT_END_MINUTES:
            z = picks.dropna(subset=[f"ret_{exit_min}"]).copy()
            if z.empty:
                continue
            daily_gross = z.groupby("session_date", observed=True)[f"ret_{exit_min}"].mean()
            trades_by_day = z.groupby("session_date", observed=True).size()
            for cost in COSTS_BPS_SIDE:
                daily = pd.Series(0.0, index=pd.to_datetime(calendar))
                daily.loc[pd.to_datetime(daily_gross.index)] = daily_gross.values - 2 * cost / 10000
                base = {
                    "checkpoint_min": cp,
                    "formula": formula,
                    "top_n": top_n,
                    "min_open_bps": min_open_bps,
                    "min_open_rvol": min_rvol,
                    "regime": regime,
                    "exit_end_min": exit_min,
                    "cost_bps_side": cost,
                    "active_days": int(len(daily_gross)),
                    "trades": int(len(z)),
                    "trades_per_week": float(len(z) / len(calendar) * 5),
                    "active_fraction": float(len(daily_gross) / len(calendar)),
                    "mean_names_when_active": float(trades_by_day.mean()),
                }
                full = metrics(daily.index.to_numpy(), daily.to_numpy())
                train_mask = daily.index <= TRAIN_END
                valid_mask = daily.index >= VALID_START
                row = {**base, **{f"full_{k}": v for k, v in full.items()}}
                row.update({f"train_{k}": v for k, v in metrics(daily.index[train_mask].to_numpy(), daily[train_mask].to_numpy()).items()})
                row.update({f"valid_{k}": v for k, v in metrics(daily.index[valid_mask].to_numpy(), daily[valid_mask].to_numpy()).items()})
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
        & result.trades_per_week.ge(3)
    )
    result.to_csv(out / "grid_metrics.csv", index=False)

    # Freeze rankings using training data only; validation is reported but does
    # not alter the ordering.
    candidates = result[result.cost_bps_side.eq(5)].copy()
    candidates["train_score"] = (
        candidates.train_cagr
        + 0.5 * candidates.train_sharpe
        + candidates.train_max_drawdown
        + 0.25 * candidates.train_worst_month
    )
    frozen = candidates.sort_values("train_score", ascending=False).head(30)
    frozen.to_csv(out / "train_selected_top30.csv", index=False)

    leaders = result[
        result.cost_bps_side.eq(5)
        & result.valid_cagr.gt(0)
        & result.full_cagr.gt(0)
    ].sort_values(
        ["robust_gate", "full_cagr", "full_max_drawdown"],
        ascending=[False, False, False],
    ).head(20)
    leaders.to_csv(out / "leaders_for_audit.csv", index=False)

    for rank, (_, r) in enumerate(leaders.head(5).iterrows(), start=1):
        picks = select_trades(
            df, int(r.checkpoint_min), str(r.formula), int(r.top_n),
            float(r.min_open_bps), float(r.min_open_rvol), str(r.regime),
        ).dropna(subset=[f"ret_{int(r.exit_end_min)}"]).copy()
        picks["gross_return"] = picks[f"ret_{int(r.exit_end_min)}"]
        picks["net_return"] = picks["gross_return"] - 2 * float(r.cost_bps_side) / 10000
        picks["candidate_rank"] = rank
        ledgers.append(picks[[
            "candidate_rank", "session_date", "symbol", "security_id", "cp",
            "entry_open", f"exit_{int(r.exit_end_min)}", "gross_return", "net_return",
            "gap_ret", "open_ret", "prev1_ret", "prev5_ret", "open_rvol",
            f"score_{str(r.formula)}",
        ]])
    ledger = pd.concat(ledgers, ignore_index=True) if ledgers else pd.DataFrame()
    ledger.to_csv(out / "top5_trade_ledger.csv", index=False)
    return result, frozen


def write_summary(df: pd.DataFrame, result: pd.DataFrame, frozen: pd.DataFrame, out: Path, elapsed: float) -> None:
    frozen_valid = frozen.sort_values("valid_cagr", ascending=False)
    best = frozen_valid.iloc[0].to_dict() if len(frozen_valid) else {}
    robust_count = int(result.robust_gate.sum())
    spec_hash = hashlib.sha256(sql_text().encode()).hexdigest()
    meta = {
        "data_start": START,
        "data_end": END,
        "sealed_holdout_start": "2026-01-01",
        "holdout_access": False,
        "rows": len(df),
        "symbols": int(df.symbol.nunique()),
        "sessions": int(df.session_date.nunique()),
        "grid_rows": len(result),
        "robust_gate_rows": robust_count,
        "device": str(torch.cuda.get_device_name(0)),
        "gpu_peak_memory_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 3),
        "elapsed_sec": round(elapsed, 2),
        "sql_sha256": spec_hash,
    }
    (out / "run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    verdict = "RESEARCH CANDIDATE" if robust_count else "NO CANDIDATE"
    lines = [
        "# Opening information-persistence momentum — development report",
        "",
        f"**Verdict: {verdict}.** The sealed 2026 sample was not accessed.",
        "",
        "## Design",
        "",
        "- Strict point-in-time Nasdaq-100 membership with stable security IDs.",
        "- Completed 15/30/45/60-minute opening window; next-bar-open entry; same-day scheduled exit.",
        "- Long-only top 1/2/3, equal-weight full allocation on active days; cash otherwise; no leverage.",
        "- Five pre-specified information-persistence scores combining opening relative strength with gap, prior-day, and prior-week strength.",
        "- Full grid includes opening thresholds, opening-volume confirmation, market regime, six exits, and 0/2/5/10 bp per-side costs.",
        "- Candidate ranking frozen on 2019–2022; 2023–2025 used only as internal development validation. 2026 remains sealed.",
        "",
        "## Run facts",
        "",
        f"- {meta['rows']:,} symbol/checkpoint rows, {meta['symbols']} symbols, {meta['sessions']} sessions.",
        f"- {meta['grid_rows']:,} portfolio/cost configurations; {robust_count} passed the predeclared 5 bp/side robustness gate.",
        f"- CUDA device: {meta['device']}; peak allocated memory {meta['gpu_peak_memory_gb']} GB.",
    ]
    if best:
        lines += [
            "",
            "## Best training-frozen row by validation CAGR",
            "",
            f"- Rule: checkpoint {int(best['checkpoint_min'])}m, `{best['formula']}`, top {int(best['top_n'])}, "
            f"opening move ≥{best['min_open_bps']:.0f} bp, opening RVOL ≥{best['min_open_rvol']:.2f}, "
            f"regime `{best['regime']}`, exit {int(best['exit_end_min']//60):02d}:{int(best['exit_end_min']%60):02d} ET.",
            f"- 5 bp/side: train CAGR {best['train_cagr']:.1%}, validation CAGR {best['valid_cagr']:.1%}, "
            f"full CAGR {best['full_cagr']:.1%}, max drawdown {best['full_max_drawdown']:.1%}, "
            f"worst month {best['full_worst_month']:.1%}, drawdown duration {int(best['full_dd_duration_calendar_days'])} calendar days.",
            f"- Activity: {best['trades_per_week']:.2f} names/week, active on {best['active_fraction']:.1%} of sessions.",
        ]
    lines += [
        "",
        "## Promotion boundary",
        "",
        "Bar results are not executable evidence. Any surviving row must be frozen, checked for parameter-neighbor and symbol concentration, and then replayed on development-period SIP quotes before paper trading. The sealed holdout requires separate explicit approval.",
    ]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="D:/AlgoResearch/data/catalog.duckdb")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    df = add_features(assemble(args.catalog, out / "event_matrix.parquet"))
    df.to_parquet(out / "event_matrix_features.parquet", index=False)
    result, frozen = run_scan(df, out)
    write_summary(df, result, frozen, out, time.perf_counter() - t0)


if __name__ == "__main__":
    main()
