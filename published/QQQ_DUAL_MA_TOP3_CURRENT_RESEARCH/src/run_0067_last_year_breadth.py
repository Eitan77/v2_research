from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0600" / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from run_0033_exit_overlays import base_context

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0067"
COST = 9.740340418 / 10000.0
END = pd.Timestamp("2026-08-14")
START = pd.Timestamp("2025-08-15")
OOS_START = pd.Timestamp("2026-05-01")


def pull_adjusted(symbols):
    cache = OUT / "adjusted_extension.parquet"
    if cache.exists():
        frame = pd.read_parquet(cache)
        frame["date"] = pd.to_datetime(frame.date)
        return frame
    env = {}
    for raw_line in (ROOT / ".env.local").read_text(encoding="utf-8").splitlines():
        if "=" in raw_line and not raw_line.lstrip().startswith("#"):
            key, value = raw_line.split("=", 1)
            env[key.strip()] = value.strip().strip("\"'")
    session = requests.Session()
    session.headers.update({"APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"],
                            "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"]})
    url = env.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/") + "/v2/stocks/bars"
    rows = []
    for offset in range(0, len(symbols), 50):
        token = None
        while True:
            params = {"symbols": ",".join(symbols[offset:offset + 50]), "timeframe": "1Day",
                      "start": "2026-04-30T00:00:00Z", "end": "2026-08-15T00:00:00Z",
                      "adjustment": "all", "feed": "sip", "sort": "asc", "limit": 10000}
            if token:
                params["page_token"] = token
            for attempt in range(8):
                response = session.get(url, params=params, timeout=90)
                if response.status_code == 429 or response.status_code >= 500:
                    time.sleep(min(15, 1 + 2 * attempt))
                    continue
                response.raise_for_status()
                break
            payload = response.json()
            for symbol, bars in (payload.get("bars") or {}).items():
                for bar in bars:
                    rows.append({"date": str(bar["t"])[:10], "symbol": symbol,
                                 "open": bar["o"], "close": bar["c"]})
            token = payload.get("next_page_token")
            if not token:
                break
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame.date)
    frame = frame.drop_duplicates(["date", "symbol"]).sort_values(["date", "symbol"])
    if frame.empty or frame.date.max() != END or (frame.date > END).any():
        raise RuntimeError("adjusted Alpaca pull boundary failure")
    frame.to_parquet(cache, index=False)
    return frame


def extension(p):
    con = duckdb.connect(r"D:\AlgoResearch\data\catalog.duckdb", read_only=True)
    adjusted = pull_adjusted(p.symbols.astype(str).tolist())
    raw = con.execute("""
        select date, symbol,
               arg_max(close, try_cast(ingested_at as timestamp)) as raw_close,
               arg_max(volume, try_cast(ingested_at as timestamp)) as raw_volume
        from bars_1d
        where date between date '2026-04-30' and date '2026-08-14'
          and feed='sip' and adjustment='raw'
        group by 1,2
    """).fetchdf()
    membership = con.execute("""
        select try_cast(date as date) as date, symbol,
               arg_max(is_member, try_cast(ingested_at as timestamp)) as is_member
        from qqq_pit_membership_daily
        where try_cast(date as date) between date '2026-04-30' and date '2026-08-14'
        group by 1,2
    """).fetchdf()
    con.close()
    for frame in (adjusted, raw, membership):
        frame["date"] = pd.to_datetime(frame.date)
    if adjusted.empty or adjusted.date.max() != END:
        raise RuntimeError(f"adjusted data does not reach {END.date()}: {adjusted.date.max() if len(adjusted) else None}")
    ext_dates = pd.DatetimeIndex(sorted(adjusted.loc[adjusted.date > pd.Timestamp("2026-04-30"), "date"].unique()))
    symbols = p.symbols.astype(str)
    m = len(symbols)
    op = np.full((len(ext_dates), m), np.nan)
    cl = np.full_like(op, np.nan)
    dv = np.full_like(op, np.nan)
    member = np.zeros_like(op, dtype=bool)
    membership_max = pd.Timestamp(membership.date.max())
    for c, symbol in enumerate(symbols):
        a = adjusted[adjusted.symbol.eq(symbol)].set_index("date")
        anchor = a.loc[pd.Timestamp("2026-04-30")] if pd.Timestamp("2026-04-30") in a.index else None
        if anchor is not None and np.isfinite(p.adj_close[-1, c]) and float(anchor.close) > 0:
            scale = float(p.adj_close[-1, c] / float(anchor.close))
            op[:, c] = a.open.reindex(ext_dates).to_numpy(dtype=float) * scale
            cl[:, c] = a.close.reindex(ext_dates).to_numpy(dtype=float) * scale
        r = raw[raw.symbol.eq(symbol)].set_index("date")
        dv[:, c] = (r.raw_close * r.raw_volume).reindex(ext_dates).to_numpy(dtype=float)
        g = membership[membership.symbol.eq(symbol)].set_index("date").is_member
        if len(g):
            member[:, c] = g.reindex(ext_dates).ffill().fillna(False).to_numpy(dtype=bool)
    return ext_dates, op, cl, dv, member, membership_max


def signals(dates):
    periods = dates.to_period("W-FRI")
    return np.array([i for i in range(len(dates) - 1) if periods[i + 1] != periods[i]], dtype=int)


def evaluate(dates, returns, score, mask, top_n, start, end):
    sig = signals(dates)
    decisions = np.zeros_like(score)
    for i in sig:
        candidates = np.flatnonzero(mask[i] & np.isfinite(score[i]))
        if len(candidates):
            chosen = candidates[np.argsort(score[i, candidates], kind="stable")[-min(top_n, len(candidates)):]]
            decisions[i, chosen] = 1.0 / len(chosen)
    target = np.zeros_like(score)
    current = np.zeros(score.shape[1])
    sig_set = set(sig.tolist())
    for i in range(len(dates)):
        if i in sig_set:
            current = decisions[i].copy()
        target[i] = current
    executed = np.zeros_like(target)
    executed[1:] = target[:-1]
    pnl = np.zeros(len(dates))
    turnover = np.zeros(len(dates))
    previous = np.zeros(score.shape[1])
    for i in range(len(dates)):
        turnover[i] = np.abs(executed[i] - previous).sum()
        previous = executed[i].copy()
        pnl[i] = np.nansum(executed[i] * np.nan_to_num(returns[i], nan=0.0)) - turnover[i] * COST
    window = (dates >= start) & (dates <= end)
    w_pnl = pnl[window]
    equity = 1.0 + np.cumsum(w_pnl)
    peaks = np.maximum.accumulate(np.r_[1.0, equity])[1:]
    return float(w_pnl.sum()), float(np.max((peaks - equity) / peaks)), int((turnover[window] > 1e-12).sum())


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    p, hist_score, hist_mask, _, _, _, _ = base_context()
    ext_dates, ext_open, ext_close, ext_dv, ext_member, membership_max = extension(p)
    dates = pd.DatetimeIndex(list(pd.DatetimeIndex(p.dates)) + list(ext_dates))
    op = np.vstack([p.adj_open, ext_open])
    cl = np.vstack([p.adj_close, ext_close])
    returns = np.full_like(op, np.nan)
    returns[:len(p.dates)] = p.open_to_next_open_return
    # Replace the discovery panel's terminal open-to-close row with the true
    # Apr-30 to May-1 carry, then extend using Alpaca adjustment=all.
    returns[len(p.dates) - 1] = np.divide(ext_open[0], p.adj_open[-1], out=np.zeros(op.shape[1]),
                                          where=np.isfinite(ext_open[0]) & np.isfinite(p.adj_open[-1]) & (p.adj_open[-1] > 0)) - 1.0
    for j in range(len(ext_dates)):
        i = len(p.dates) + j
        if j + 1 < len(ext_dates):
            returns[i] = np.divide(ext_open[j + 1], ext_open[j], out=np.zeros(op.shape[1]),
                                    where=np.isfinite(ext_open[j + 1]) & np.isfinite(ext_open[j]) & (ext_open[j] > 0)) - 1.0
        else:
            returns[i] = np.divide(ext_close[j], ext_open[j], out=np.zeros(op.shape[1]),
                                    where=np.isfinite(ext_close[j]) & np.isfinite(ext_open[j]) & (ext_open[j] > 0)) - 1.0
    close_df = pd.DataFrame(cl, index=dates)
    dv_df = pd.DataFrame(np.vstack([p.raw_close * p.volume, ext_dv]), index=dates)
    sma50 = close_df.rolling(50, min_periods=50).mean().to_numpy()
    sma200 = close_df.rolling(200, min_periods=200).mean().to_numpy()
    dv63 = dv_df.rolling(63, min_periods=32).median().to_numpy()
    score = np.full_like(cl, np.nan)
    score[:len(p.dates)] = hist_score
    tri = np.ones_like(cl)
    tri[:len(p.dates)] = p.total_return_index
    last = p.total_return_index[-1].copy()
    previous_close = p.adj_close[-1].copy()
    for j in range(len(ext_dates)):
        i = len(p.dates) + j
        step = np.divide(cl[i], previous_close, out=np.ones(cl.shape[1]),
                         where=np.isfinite(cl[i]) & np.isfinite(previous_close) & (previous_close > 0))
        last = last * step
        tri[i] = last
        previous_close = cl[i]
        if i >= 147:
            score[i] = tri[i - 21] / tri[i - 147] - 1.0
    mask = np.zeros_like(cl, dtype=bool)
    mask[:len(p.dates)] = hist_mask
    for j, _ in enumerate(ext_dates):
        i = len(p.dates) + j
        ready = ext_member[j] & np.isfinite(cl[i]) & (sma50[i] > sma200[i]) & np.isfinite(score[i]) & np.isfinite(dv63[i])
        eligible = np.flatnonzero(ready)
        keep = max(1, int(np.ceil(len(eligible) * 0.5))) if len(eligible) else 0
        liquid = eligible[np.argsort(dv63[i, eligible], kind="stable")[-keep:]] if keep else np.array([], dtype=int)
        mask[i, liquid] = True
    rows = []
    reproduction = []
    pre_returns = returns.copy()
    pre_returns[len(p.dates) - 1] = p.open_to_close_return[-1]
    controls = pd.read_csv(ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0029" / "breadth_curve.csv")
    max_control_error = 0.0
    for n in range(1, 21):
        pre_ret, _, _ = evaluate(dates, pre_returns, score, mask, n, pd.Timestamp("2025-05-01"), pd.Timestamp("2026-04-30"))
        expected = float(controls.loc[controls.top_n.eq(n), "recent12_return"].iloc[0])
        max_control_error = max(max_control_error, abs(pre_ret - expected))
        reproduction.append({"top_n": n, "calculated": pre_ret, "expected": expected, "error": pre_ret - expected})
        total, dd, trade_sessions = evaluate(dates, returns, score, mask, n, START, END)
        rows.append({"top_n": n, "total_return": total, "maximum_drawdown": dd, "trade_sessions": trade_sessions})
    if max_control_error > 1e-8:
        print(pd.DataFrame(reproduction).to_string(index=False))
        raise RuntimeError(f"RUN-0029 reproduction failed: max error {max_control_error}")
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / "last_year_breadth.csv", index=False)
    report = {"status": "completed", "window_start": str(START.date()), "window_end": str(END.date()),
              "observed_oos_start": str(OOS_START.date()), "maximum_loaded_date": str(dates.max().date()),
              "membership_maximum_date": str(membership_max.date()),
              "membership_carry_forward_sessions": int((ext_dates > membership_max).sum()),
              "run0029_max_reproduction_error": max_control_error, "holdout_label": "already_observed_descriptive_not_fresh_OOS",
              "metrics": rows}
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(frame.to_string(index=False))
    print(json.dumps({k: v for k, v in report.items() if k != "metrics"}, indent=2))


if __name__ == "__main__":
    main()
