from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0600" / "src"))
from run_0033_exit_overlays import base_context
from run_0067_last_year_breadth import extension

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0069"
END = pd.Timestamp("2026-08-14")


def credentials():
    env = {}
    for line in (ROOT / ".env.local").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("\"'")
    return env


def raw_extension(symbols):
    cache = OUT / "raw_extension.parquet"
    if cache.exists():
        x = pd.read_parquet(cache)
        x["date"] = pd.to_datetime(x.date)
        return x
    env = credentials()
    session = requests.Session()
    session.headers.update({"APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"],
                            "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"]})
    url = env.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/") + "/v2/stocks/bars"
    rows = []
    for offset in range(0, len(symbols), 50):
        token = None
        while True:
            params = {"symbols": ",".join(symbols[offset:offset+50]), "timeframe": "1Day",
                      "start": "2026-04-30T00:00:00Z", "end": "2026-08-15T00:00:00Z",
                      "adjustment": "raw", "feed": "sip", "sort": "asc", "limit": 10000}
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
                                 "close": bar["c"], "volume": bar["v"]})
            token = payload.get("next_page_token")
            if not token:
                break
    x = pd.DataFrame(rows)
    x["date"] = pd.to_datetime(x.date)
    x = x.drop_duplicates(["date", "symbol"]).sort_values(["date", "symbol"])
    if x.empty or x.date.max() != END or (x.date > END).any():
        raise RuntimeError("raw extension boundary failure")
    OUT.mkdir(parents=True, exist_ok=True)
    x.to_parquet(cache, index=False)
    return x


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    p, hist_score, _, _, _, _, _ = base_context()
    ext_dates, _, ext_close, _, ext_member, membership_max = extension(p)
    dates = pd.DatetimeIndex(list(pd.DatetimeIndex(p.dates)) + list(ext_dates))
    close = np.vstack([p.adj_close, ext_close])
    raw = raw_extension([str(s) for s in p.symbols])
    raw_dv = np.full((len(ext_dates), len(p.symbols)), np.nan)
    for c, symbol in enumerate(p.symbols.astype(str)):
        z = raw[raw.symbol.eq(symbol)].set_index("date")
        raw_dv[:, c] = (z.close * z.volume).reindex(ext_dates).to_numpy(dtype=float)
    dv = np.vstack([p.raw_close * p.volume, raw_dv])
    sma50 = pd.DataFrame(close).rolling(50, min_periods=50).mean().to_numpy()
    sma200 = pd.DataFrame(close).rolling(200, min_periods=200).mean().to_numpy()
    dv63 = pd.DataFrame(dv).rolling(63, min_periods=32).median().to_numpy()
    score = np.full_like(close, np.nan)
    score[:len(p.dates)] = hist_score
    tri = np.ones_like(close)
    tri[:len(p.dates)] = p.total_return_index
    last, previous_close = p.total_return_index[-1].copy(), p.adj_close[-1].copy()
    for j in range(len(ext_dates)):
        i = len(p.dates) + j
        step = np.divide(close[i], previous_close, out=np.ones(close.shape[1]),
                         where=np.isfinite(close[i]) & np.isfinite(previous_close) & (previous_close > 0))
        last *= step
        tri[i] = last
        previous_close = close[i]
        if i >= 147:
            score[i] = tri[i-21] / tri[i-147] - 1.0
    i = len(dates) - 1
    member = ext_member[-1]
    ready = member & np.isfinite(close[i]) & (sma50[i] > sma200[i]) & np.isfinite(score[i]) & np.isfinite(dv63[i])
    eligible = np.flatnonzero(ready)
    keep = max(1, int(np.ceil(len(eligible) * 0.5))) if len(eligible) else 0
    liquid = eligible[np.argsort(dv63[i, eligible], kind="stable")[-keep:]] if keep else np.array([], dtype=int)
    ranked = liquid[np.argsort(score[i, liquid], kind="stable")[::-1]]
    rows = []
    for rank, c in enumerate(ranked, 1):
        rows.append({"rank": rank, "symbol": str(p.symbols[c]), "momentum_126_skip21": float(score[i, c]),
                     "sma50": float(sma50[i, c]), "sma200": float(sma200[i, c]),
                     "median_dollar_volume_63": float(dv63[i, c])})
    pd.DataFrame(rows).to_csv(OUT / "ranking_2026-08-14.csv", index=False)
    report = {"status": "completed", "signal_date": str(dates[i].date()),
              "intended_execution": "2026-08-17 09:40 America/New_York",
              "maximum_loaded_date": str(raw.date.max().date()), "membership_maximum_date": str(membership_max.date()),
              "trend_eligible_count": int(len(eligible)), "liquid_half_count": int(len(liquid)),
              "top3": [row["symbol"] for row in rows[:3]], "ranking": rows[:10]}
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
