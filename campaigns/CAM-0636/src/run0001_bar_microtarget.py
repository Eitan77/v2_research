from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
CAM = ROOT / "campaigns" / "CAM-0636"
OUT = CAM / "artifacts" / "RUN-0001"
CATALOG = Path(r"D:\AlgoResearch\data\catalog.duckdb")
START = pd.Timestamp("2025-05-01")
END = pd.Timestamp("2025-07-31")
GREEN_BP = [0, 5, 10, 20]
RVOL = [0.0, 1.0, 1.5, 2.0]
TARGET_BP = [2, 5, 10, 15]
HOLDS = [1, 3, 5, 10]
COST_BP = [2, 5]


def load() -> pd.DataFrame:
    con = duckdb.connect(str(CATALOG), read_only=True)
    q = """
      select date, try_cast(timestamp as timestamptz) ts,
             arg_max(open, try_cast(ingested_at as timestamp)) as open,
             arg_max(high, try_cast(ingested_at as timestamp)) as high,
             arg_max(close, try_cast(ingested_at as timestamp)) as close,
             arg_max(volume, try_cast(ingested_at as timestamp)) as volume
      from bars_1m
      where date between date '2025-05-01' and date '2025-07-31'
        and feed='sip' and adjustment='raw' and symbol='SOXL'
        and strftime(try_cast(timestamp as timestamptz) at time zone 'America/New_York','%H:%M') between '09:30' and '15:59'
      group by 1,2 order by 1,2
    """
    x = con.execute(q).fetchdf()
    con.close()
    x["date"] = pd.to_datetime(x.date)
    x["ts"] = pd.to_datetime(x.ts, utc=True)
    if x.empty or x.date.min() < START or x.date.max() > END:
        raise RuntimeError("date-bound readiness failure")
    if x.duplicated(["date", "ts"]).any():
        raise RuntimeError("duplicate bars")
    x["hhmm"] = x.ts.dt.tz_convert("America/New_York").dt.strftime("%H:%M")
    x["green_bp"] = (x.close / x.open - 1.0) * 1e4
    x["clock_vol_med20"] = x.groupby("hhmm").volume.transform(lambda s: s.shift(1).rolling(20, min_periods=10).median())
    x["rvol"] = x.volume / x.clock_vol_med20
    return x


def metrics(trades: pd.DataFrame, sessions: pd.DatetimeIndex) -> dict:
    if trades.empty:
        return {"trades": 0, "net_return": 0.0}
    daily = trades.groupby("date").net_return.sum().reindex(sessions, fill_value=0.0)
    eq = 1.0 + daily.cumsum(); peak = eq.cummax(); dd = ((peak - eq) / peak).max()
    monthly = daily.resample("ME").sum()
    return {
        "trades": int(len(trades)), "trades_per_session": float(len(trades)/len(sessions)),
        "net_return": float(trades.net_return.sum()), "mean_trade_bp": float(trades.net_return.mean()*1e4),
        "target_fill_proxy_rate": float(trades.target_hit.mean()), "forced_exit_rate": float((~trades.target_hit).mean()),
        "green_trade_fraction": float((trades.net_return > 0).mean()), "positive_months": int((monthly > 0).sum()),
        "max_drawdown": float(dd), "monthly": {str(k.date()): float(v) for k,v in monthly.items()},
    }


def simulate(x: pd.DataFrame, green: int, rvol: float, target: int, hold: int, cost: int) -> pd.DataFrame:
    rows = []
    for date, g in x.groupby("date", sort=True):
        g = g.reset_index(drop=True)
        hhmm = g.hhmm.to_numpy()
        green_values = g.green_bp.to_numpy(dtype=float)
        rvol_values = g.rvol.to_numpy(dtype=float)
        opens = g.open.to_numpy(dtype=float)
        highs = g.high.to_numpy(dtype=float)
        closes = g.close.to_numpy(dtype=float)
        stamps = g.ts.to_numpy()
        eligible = (green_values >= green) & (hhmm >= "09:35") & (hhmm <= "15:45")
        if rvol > 0:
            eligible &= np.isfinite(rvol_values) & (rvol_values >= rvol)
        candidates = np.flatnonzero(eligible & (np.arange(len(g)) < len(g)-hold-1))
        last_exit = -1
        for i in candidates:
            if i <= last_exit:
                continue
            entry_i = i + 1; exit_i = entry_i + hold - 1
            entry_raw = opens[entry_i]; entry = entry_raw * (1 + cost/1e4)
            limit = entry * (1 + target/1e4)
            hits = np.flatnonzero(highs[entry_i:exit_i+1] >= limit)
            hit = len(hits) > 0
            actual_exit_i = entry_i + int(hits[0]) if hit else exit_i
            exit_px = limit if hit else closes[exit_i] * (1 - cost/1e4)
            rows.append({"date":date,"signal_ts":stamps[i],"entry_ts":stamps[entry_i],"exit_ts":stamps[actual_exit_i],
                         "green_bp":green_values[i],"rvol":rvol_values[i],"entry":entry,"exit":exit_px,
                         "target_hit":hit,"net_return":exit_px/entry-1})
            last_exit = actual_exit_i
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    x = load(); sessions = pd.DatetimeIndex(sorted(x.date.unique()))
    results=[]; ledgers={}
    for green,rvol,target,hold,cost in product(GREEN_BP,RVOL,TARGET_BP,HOLDS,COST_BP):
        name=f"g{green}_rv{rvol:g}_t{target}_h{hold}_c{cost}"
        t=simulate(x,green,rvol,target,hold,cost); m=metrics(t,sessions)
        results.append({"variant":name,"green_bp":green,"rvol":rvol,"target_bp":target,"hold":hold,"cost_bp":cost,**{k:v for k,v in m.items() if k!='monthly'}})
        ledgers[name]=(t,m)
    grid=pd.DataFrame(results).sort_values(["cost_bp","net_return"],ascending=[True,False])
    grid.to_csv(OUT/"grid.csv",index=False)
    top5=grid[(grid.cost_bp==5)&(grid.trades>=30)].head(10)
    for name in top5.variant:
        ledgers[name][0].to_csv(OUT/f"ledger_{name}.csv",index=False)
    report={"loaded":{"rows":len(x),"sessions":len(sessions),"min_date":str(x.date.min().date()),"max_date":str(x.date.max().date()),"missing_volume_baseline_rows":int(x.clock_vol_med20.isna().sum())},
            "executed_variants":len(grid),"expected_variants":len(GREEN_BP)*len(RVOL)*len(TARGET_BP)*len(HOLDS)*len(COST_BP),
            "best_cost5_min30":top5.to_dict("records"),"top_monthly":{name:ledgers[name][1]["monthly"] for name in top5.variant}}
    (OUT/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))


if __name__ == "__main__": main()
