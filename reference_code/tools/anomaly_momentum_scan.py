"""Causal opening-move event study with CUDA evaluation.

The database only assembles the event matrix.  All conditional statistics and
cost grids are evaluated as tensors on the GPU.  Signals use a completed
opening bar and enter on the following bar's open.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import torch


HORIZONS = tuple(range(5, 386, 5))
EARLY = (5, 10, 15)
RANK_LEVELS = (0.005, 0.01, 0.02, 0.05, 0.10, 0.20, 0.50)
MAG_BPS = (0.0, 5.0, 10.0, 25.0, 50.0, 100.0, 200.0, 400.0)
COSTS = (0.0, 2.0, 5.0, 10.0, 25.0, 50.0)


def load_events(catalog: str, start: str, end: str) -> pd.DataFrame:
    hsql = ",\n".join(
        f"max(case when f.et_min = e.entry_min + {h - 5} then f.close / e.entry_open - 1.0 end) as fwd_{h}"
        for h in HORIZONS
    )
    # ET minute is computed from the provider timestamp, not inferred from row
    # order.  The signal bar is complete before the entry bar starts.
    sql = f"""
    with b as (
      select symbol, session_date, cast(timestamp as timestamptz) at time zone 'America/New_York' as et,
             open, high, low, close, volume, relative_volume_20, atr_pct_14
      from research_matrix
      where timeframe = '5m'
        and session_date >= ? and session_date <= ?
        and extract(hour from (cast(timestamp as timestamptz) at time zone 'America/New_York')) * 60
            + extract(minute from (cast(timestamp as timestamptz) at time zone 'America/New_York')) between 570 and 955
    ), x as (
      select *, extract(hour from et) * 60 + extract(minute from et) as et_min
      from b
    ), opens as (
      select symbol, session_date, max(open) filter (where et_min = 570) as session_open
      from x group by symbol, session_date
    ), signals as (
      select s.symbol, s.session_date, s.et_min - 570 + 5 as early_min,
             s.close / o.session_open - 1.0 as early_move,
             s.relative_volume_20, s.atr_pct_14,
             e.et_min as entry_min, e.open as entry_open
      from x s join opens o using(symbol, session_date)
      join x e on e.symbol = s.symbol and e.session_date = s.session_date and e.et_min = s.et_min + 5
      where s.et_min in (570, 575, 580) and o.session_open > 0 and e.open > 0
    ), ranked as (
      select *, percent_rank() over (partition by session_date, early_min order by early_move) as rank_pct
      from signals
    )
    select e.symbol, e.session_date, e.early_min, e.early_move * 10000.0 as early_bps,
           e.rank_pct, e.relative_volume_20, e.atr_pct_14, e.entry_min, e.entry_open,
           {hsql}
    from ranked e join x f on f.symbol = e.symbol and f.session_date = e.session_date
      and f.et_min between e.entry_min and 955
    group by e.symbol, e.session_date, e.early_min, e.early_move, e.rank_pct,
             e.relative_volume_20, e.atr_pct_14, e.entry_min, e.entry_open
    order by e.session_date, e.early_min, e.symbol
    """
    con = duckdb.connect(catalog, read_only=True)
    try:
        con.execute("set threads = 16")
        con.execute("set temp_directory = 'D:/AlgoResearch/work/duck_tmp'")
        return con.execute(sql, [start, end]).fetchdf()
    finally:
        con.close()


def max_dd(simple_returns: np.ndarray) -> float:
    if len(simple_returns) == 0:
        return float("nan")
    eq = np.cumsum(np.nan_to_num(simple_returns, nan=0.0))
    return float(np.min(eq - np.maximum.accumulate(eq)))


def run(df: pd.DataFrame, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        raise RuntimeError("CUDA is required for this anomaly scan")
    df["year"] = pd.to_datetime(df["session_date"]).dt.year
    # Keep only complete forward paths through the requested horizon.
    fcols = [f"fwd_{h}" for h in HORIZONS]
    mat = df[fcols].to_numpy(dtype=np.float32)
    tmat = torch.as_tensor(np.nan_to_num(mat, nan=0.0), device=device)
    valid = torch.as_tensor(np.isfinite(mat), device=device)
    early = df["early_min"].to_numpy(np.int16)
    early_t = torch.as_tensor(early, device=device)
    bps = torch.as_tensor(df["early_bps"].to_numpy(np.float32), device=device)
    rank = torch.as_tensor(df["rank_pct"].to_numpy(np.float32), device=device)
    dates = df["session_date"].astype(str).to_numpy()
    rows: list[dict] = []
    t0 = time.perf_counter()
    for em in EARLY:
        emask = early_t.eq(em)
        for direction in ("up", "down"):
            dmask = bps.ge(0) if direction == "up" else bps.le(0)
            for level in RANK_LEVELS:
                rmask = rank.ge(1.0 - level) if direction == "up" else rank.le(level)
                for mag in MAG_BPS:
                    mmask = bps.ge(mag) if direction == "up" else bps.le(-mag)
                    mask = emask & dmask & rmask & mmask
                    n = int(mask.sum().item())
                    if n < 30:
                        continue
                    x = tmat[mask]
                    v = valid[mask]
                    count = v.sum(dim=0).clamp_min(1)
                    for hi, h in enumerate(HORIZONS):
                        z = x[:, hi][v[:, hi]]
                        if z.numel() < 30:
                            continue
                        # fixed-notional cost is subtracted per round trip;
                        # return columns are gross entry-open to exit-close.
                        for cost in COSTS:
                            net = z - (2.0 * cost / 10000.0)
                            rows.append({
                                "early_min": em, "direction": direction, "rank_level": level,
                                "magnitude_bps": mag, "horizon_min": h, "cost_bps_side": cost,
                                "n_events": int(z.numel()), "mean_return": float(net.mean().item()),
                                "median_return": float(net.median().item()), "win_rate": float((net > 0).float().mean().item()),
                                "p10": float(torch.quantile(net, 0.10).item()), "p90": float(torch.quantile(net, 0.90).item()),
                            })
    result = pd.DataFrame(rows)
    result.to_csv(out / "anomaly_forward_returns.csv", index=False)
    # For each broad configuration, summarize fixed-capital one-trade-per-day
    # behavior using the most extreme qualifying mover on each date.
    summary = []
    for em in EARLY:
        for direction in ("up", "down"):
            for level in RANK_LEVELS:
                for mag in MAG_BPS:
                    m = (df.early_min.eq(em) & (df.early_bps.ge(mag) if direction == "up" else df.early_bps.le(-mag))
                         & (df.rank_pct.ge(1-level) if direction == "up" else df.rank_pct.le(level)))
                    q = df.loc[m].copy()
                    if q.empty:
                        continue
                    q["score"] = q["early_bps"] if direction == "up" else -q["early_bps"]
                    q = q.sort_values(["session_date", "score"], ascending=[True, False]).drop_duplicates("session_date")
                    for h in HORIZONS:
                        vals = q[f"fwd_{h}"].dropna().to_numpy(np.float64)
                        if len(vals) < 60:
                            continue
                        for cost in COSTS:
                            net = vals - 2 * cost / 10000.0
                            summary.append({"early_min": em,"direction":direction,"rank_level":level,"magnitude_bps":mag,
                                            "horizon_min":h,"cost_bps_side":cost,"days":len(net),
                                            "days_per_week":len(net)/max(df.session_date.nunique(),1)*5,
                                            "simple_pnl":float(net.sum()),"mean_return":float(net.mean()),
                                            "win_rate":float((net>0).mean()),"max_drawdown":max_dd(net),
                                            "positive_years":int(sum(net[q.loc[q[f'fwd_{h}'].notna(), 'year'].to_numpy()==y].sum()>0 for y in sorted(q.year.unique()))),
                                            "years_tested":int(q.year.nunique())})
    pd.DataFrame(summary).to_csv(out / "fixed_capital_event_summary.csv", index=False)
    meta = {"device": str(device), "rows": int(len(df)), "events": int(len(result)),
            "gpu_peak_memory_gb": round(torch.cuda.max_memory_allocated()/1024**3,3),
            "elapsed_sec": round(time.perf_counter()-t0,2), "horizons": list(HORIZONS)}
    (out / "run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="D:/AlgoResearch/data/catalog.duckdb")
    ap.add_argument("--start", default="2019-06-21")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    t0 = time.perf_counter()
    df = load_events(args.catalog, args.start, args.end)
    print(f"loaded event matrix rows={len(df)} elapsed_sec={time.perf_counter()-t0:.1f}", flush=True)
    run(df, Path(args.out))


if __name__ == "__main__":
    main()
