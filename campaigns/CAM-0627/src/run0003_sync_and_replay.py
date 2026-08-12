from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0627" / "artifacts" / "RUN-0003"
RAW = OUT / "raw_quotes"
STEP_MS = 50


def load_leg(rows: list[dict], prefix: str) -> pd.DataFrame:
    q = pd.DataFrame(rows, columns=["t", "bp", "ap", "bs", "as"])
    q["ts"] = pd.to_datetime(q.pop("t"), utc=True, format="mixed")
    q = q.sort_values("ts").drop_duplicates("ts", keep="last")
    q = q[(q.bp > 0) & (q.ap >= q.bp)]
    return q.rename(columns={c: f"{prefix}_{c}" for c in ("bp", "ap", "bs", "as")})


def synchronize(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt") as fh:
        raw = json.load(fh)
    req = raw["request"]
    start = pd.Timestamp(f"{req['date']} {req['start']}", tz="America/New_York").tz_convert("UTC")
    end = pd.Timestamp(f"{req['date']} {req['end']}", tz="America/New_York").tz_convert("UTC")
    grid = pd.DataFrame({"ts": pd.date_range(start, end, freq=f"{STEP_MS}ms", inclusive="left")})
    grid["ts"] = grid.ts.astype("datetime64[ns, UTC]")
    for symbol, prefix in (("SPY", "a"), ("IVV", "b")):
        q = load_leg(raw["quotes"][symbol], prefix)
        grid = pd.merge_asof(grid, q, on="ts", direction="backward")
        source_ts = pd.merge_asof(
            grid[["ts"]], q[["ts"]].rename(columns={"ts": "source_ts"}),
            left_on="ts", right_on="source_ts", direction="backward"
        )["source_ts"]
        grid[f"{prefix}_age_ms"] = (grid.ts - source_ts).dt.total_seconds() * 1000
    grid["date"] = req["date"]
    grid["window"] = req["label"]
    grid["mid_log_ratio"] = np.log((grid.a_bp + grid.a_ap) / (grid.b_bp + grid.b_ap))
    for seconds in (30, 60, 300):
        points = seconds * 1000 // STEP_MS
        grid[f"anchor_{seconds}"] = grid.mid_log_ratio.shift(1).rolling(points, min_periods=points).median()
    return grid


def first_true(mask: np.ndarray, start: int, stop: int) -> int | None:
    hit = np.flatnonzero(mask[start:stop])
    return None if not len(hit) else start + int(hit[0])


def simulate_window(x: pd.DataFrame, age_ms: int, anchor_sec: int, threshold_bps: int,
                    hold_sec: int, stop_bps: int) -> list[dict]:
    n = len(x)
    anchor = x[f"anchor_{anchor_sec}"].to_numpy()
    abp, aap = x.a_bp.to_numpy(), x.a_ap.to_numpy()
    bbp, bap = x.b_bp.to_numpy(), x.b_ap.to_numpy()
    valid = (x.a_age_ms.to_numpy() <= age_ms) & (x.b_age_ms.to_numpy() <= age_ms) & np.isfinite(anchor)
    rich_a_edge = np.log(abp / bap) - anchor
    rich_b_edge = anchor - np.log(aap / bbp)
    threshold = threshold_bps / 10000
    candidates = valid & ((rich_a_edge >= threshold) | (rich_b_edge >= threshold))
    max_steps = max(1, hold_sec * 1000 // STEP_MS)
    stop = stop_bps / 10000
    rows: list[dict] = []
    i = 0
    while i < n - 1:
        entry = first_true(candidates, i, n - 1)
        if entry is None:
            break
        rich_a = rich_a_edge[entry] >= rich_b_edge[entry]
        if rich_a:
            entry_short, entry_long = abp[entry], bap[entry]
        else:
            entry_short, entry_long = bbp[entry], aap[entry]
        end = min(n - 1, entry + max_steps)
        sl = slice(entry + 1, end + 1)
        valid_exit = valid[sl]
        if rich_a:
            mark = 0.5 * (1 - aap[sl] / entry_short) + 0.5 * (bbp[sl] / entry_long - 1)
            converged = np.log(aap[sl] / bbp[sl]) <= anchor[sl]
        else:
            mark = 0.5 * (1 - bap[sl] / entry_short) + 0.5 * (abp[sl] / entry_long - 1)
            converged = np.log(bap[sl] / abp[sl]) <= -anchor[sl]
        stop_hit = mark <= -stop
        event = valid_exit & (converged | stop_hit)
        rel = np.flatnonzero(event)
        exit_i = entry + 1 + int(rel[0]) if len(rel) else end
        reason = "timeout"
        if len(rel):
            reason = "stop" if stop_hit[int(rel[0])] else "convergence"
        if rich_a:
            exit_short, exit_long = aap[exit_i], bbp[exit_i]
            signal = rich_a_edge[entry]
        else:
            exit_short, exit_long = bap[exit_i], abp[exit_i]
            signal = rich_b_edge[entry]
        gross = 0.5 * (1 - exit_short / entry_short) + 0.5 * (exit_long / entry_long - 1)
        rows.append({
            "date": x.date.iloc[0], "window": x.window.iloc[0],
            "entry_ts": x.ts.iloc[entry], "exit_ts": x.ts.iloc[exit_i],
            "rich_leg": "SPY" if rich_a else "IVV", "signal_bps": signal * 10000,
            "gross_pnl": gross, "reason": reason,
        })
        i = exit_i + 1
    return rows


def metrics(trades: pd.DataFrame, bps: int) -> dict:
    if trades.empty:
        return {"trades": 0, "net_return": 0.0, "average_net_trade_bps": 0.0,
                "win_rate": 0.0, "positive_windows": 0, "negative_windows": 0,
                "positive_months": 0, "negative_months": 0, "worst_window": 0.0}
    t = trades.copy()
    t["net"] = t.gross_pnl - 2 * bps / 10000
    windows = t.groupby(["date", "window"]).net.sum()
    months = t.groupby("date").net.sum()
    return {
        "trades": len(t), "net_return": float(t.net.sum()),
        "average_net_trade_bps": float(t.net.mean() * 10000),
        "win_rate": float((t.net > 0).mean()),
        "positive_windows": int((windows > 0).sum()), "negative_windows": int((windows < 0).sum()),
        "positive_months": int((months > 0).sum()), "negative_months": int((months < 0).sum()),
        "worst_window": float(windows.min()),
    }


def main() -> None:
    paths = sorted(RAW.glob("*.json.gz"))
    if len(paths) != 72:
        raise RuntimeError(f"Expected 72 frozen windows, found {len(paths)}")
    synced = []
    for i, path in enumerate(paths, 1):
        synced.append(synchronize(path))
        if i % 6 == 0:
            print(f"synchronized={i}/{len(paths)}", flush=True)
    data = pd.concat(synced, ignore_index=True)
    data.to_parquet(OUT / "synchronized_50ms.parquet", index=False)
    grid_rows, best_trades = [], None
    for age in (50, 100, 250):
        for anchor in (30, 60, 300):
            for threshold in (1, 2, 3, 5, 10):
                for hold in (1, 5, 30, 120):
                    for stop in (5, 10, 20):
                        rows = []
                        for _, window in data.groupby(["date", "window"], sort=False):
                            rows.extend(simulate_window(window.reset_index(drop=True), age, anchor, threshold, hold, stop))
                        trades = pd.DataFrame(rows)
                        for bps in (0, 1, 2):
                            m = metrics(trades, bps)
                            m.update({"age_ms": age, "anchor_seconds": anchor,
                                      "threshold_bps": threshold, "hold_seconds": hold,
                                      "stop_bps": stop, "additional_bps_per_side": bps})
                            grid_rows.append(m)
                        if best_trades is None or (len(trades) and trades.gross_pnl.sum() > best_trades.gross_pnl.sum()):
                            best_trades = trades
    grid = pd.DataFrame(grid_rows)
    grid.to_parquet(OUT / "grid.parquet", index=False)
    best = {str(b): grid[grid.additional_bps_per_side.eq(b)].sort_values("net_return", ascending=False).iloc[0].to_dict() for b in (0, 1, 2)}
    report = {
        "status": "completed", "planned_variants": 540, "executed_variants": int(len(grid) / 3),
        "decision_grid_ms": STEP_MS, "quote_windows": 72, "synchronized_rows": len(data),
        "best_by_cost": best, "maximum_loaded_date": str(pd.to_datetime(data.date).max().date()),
        "holdout_rows_loaded": 0,
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
