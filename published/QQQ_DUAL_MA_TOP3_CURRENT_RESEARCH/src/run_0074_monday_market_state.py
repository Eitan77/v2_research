from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0611" / "src"))
sys.path.insert(0, str(ROOT / "campaigns" / "CAM-0600" / "src"))

from run_0058_self_financing import context

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0074"
CATALOG = r"D:\AlgoResearch\data\catalog.duckdb"
CUTOFF = pd.Timestamp("2026-04-30")


def market_frame(symbols=("QQQ", "SPY")):
    con = duckdb.connect(CATALOG, read_only=True)
    names = ",".join(f"'{s}'" for s in symbols)
    daily = con.execute(f"""
        select symbol, date, open, close
        from bars_1d
        where symbol in ({names}) and adjustment='raw' and feed='sip'
          and date <= '2026-04-30'
        order by symbol, date
    """).df()
    ten_parts = []
    for symbol in symbols:
        path = f"D:/AlgoResearch/data/derived/alpaca/market/stocks/bars_10m/symbol={symbol}/*.parquet"
        z = con.execute(f"""
            with dedup as (
              select *, row_number() over(
                partition by timestamp, timeframe, feed, adjustment
                order by coalesce(try_cast(ingested_at as timestamp), timestamp '1900-01-01') desc,
                         coalesce(source_ingestion_id,'') desc) as rn
              from read_parquet('{path}', union_by_name=true, hive_partitioning=false)
              where adjustment='raw' and feed='sip' and try_cast(session_date as date) <= '2026-04-30'
            )
            select '{symbol}' as symbol, try_cast(session_date as date) as date, open, close,
                   bar_start_ts, available_at_ts
            from dedup where rn=1
            qualify row_number() over(partition by session_date order by bar_start_ts)=1
            order by date
        """).df()
        ten_parts.append(z)
    ten = pd.concat(ten_parts, ignore_index=True)
    con.close()
    daily["date"] = pd.to_datetime(daily.date)
    daily["prior_close"] = daily.groupby("symbol").close.shift(1)
    daily["gap"] = daily.open / daily.prior_close - 1
    daily["prior_week"] = daily.close / daily.groupby("symbol").close.shift(5) - 1
    ten["date"] = pd.to_datetime(ten.date)
    ten["open_to_0940"] = ten.close / ten.open - 1
    x = daily.merge(ten[["symbol", "date", "open_to_0940", "available_at_ts"]],
                    on=["symbol", "date"], how="left", validate="one_to_one")
    x["prior_close_to_0940"] = (1 + x.gap) * (1 + x.open_to_0940) - 1
    return x


def cycles():
    p, schedule = context()
    if pd.Timestamp(p.dates.max()) != CUTOFF or int(p.readiness.get("holdout_rows_loaded_total", 0)):
        raise RuntimeError("discovery boundary failure")
    daily = pd.read_parquet(ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0028" /
                            "quote_daily_control_f126_s21_2bps.parquet")
    daily.date = pd.to_datetime(daily.date)
    pnl = daily.set_index("date").net_pnl.reindex(pd.DatetimeIndex(p.dates)).fillna(0.0)
    execs = sorted(i for i, target in schedule.items() if target)
    rows = []
    for k, e in enumerate(execs):
        end = execs[k + 1] - 1 if k + 1 < len(execs) else len(p.dates) - 1
        member = p.member[e] & np.isfinite(p.raw_open[e]) & np.isfinite(p.raw_close[e - 1]) & (p.raw_close[e - 1] > 0)
        gaps = p.raw_open[e, member] / p.raw_close[e - 1, member] - 1
        rows.append({
            "execution_date": pd.Timestamp(p.dates[e]),
            "end_date": pd.Timestamp(p.dates[end]),
            "cycle_net_pnl": float(pnl.iloc[e:end + 1].sum()),
            "target": "|".join(schedule[e]),
            "constituent_gap_positive_fraction": float((gaps > 0).mean()),
            "constituent_gap_median": float(np.median(gaps)),
            "constituent_count": int(len(gaps)),
        })
    frame = pd.DataFrame(rows)
    market = market_frame()
    for symbol in ("QQQ", "SPY"):
        z = market[market.symbol.eq(symbol)].drop(columns="symbol").rename(columns={
            "open": f"{symbol}_open", "close": f"{symbol}_close", "prior_close": f"{symbol}_prior_close",
            "gap": f"{symbol}_gap", "prior_week": f"{symbol}_prior_week",
            "open_to_0940": f"{symbol}_open_to_0940", "prior_close_to_0940": f"{symbol}_prior_close_to_0940",
            "available_at_ts": f"{symbol}_available_at_ts",
        })
        frame = frame.merge(z, left_on="execution_date", right_on="date", how="left", validate="one_to_one").drop(columns="date")
    required = ["QQQ_gap", "QQQ_open_to_0940", "SPY_gap", "SPY_open_to_0940"]
    if frame[required].isna().any().any():
        raise RuntimeError(f"missing market states {frame.loc[frame[required].isna().any(axis=1),'execution_date'].tolist()}")
    return frame


def summarize(z):
    return {
        "n": int(len(z)), "mean_cycle": float(z.mean()) if len(z) else None,
        "median_cycle": float(z.median()) if len(z) else None,
        "win_rate": float((z > 0).mean()) if len(z) else None,
        "worst_cycle": float(z.min()) if len(z) else None,
        "best_cycle": float(z.max()) if len(z) else None,
        "additive_contribution": float(z.sum()) if len(z) else 0.0,
    }


def analyze(frame):
    rows = [{"predictor": "all", "condition": "unconditional", **summarize(frame.cycle_net_pnl)}]
    market_predictors = [
        "QQQ_gap", "QQQ_open_to_0940", "QQQ_prior_close_to_0940", "QQQ_prior_week",
        "SPY_gap", "SPY_open_to_0940", "SPY_prior_close_to_0940", "SPY_prior_week",
        "constituent_gap_median",
    ]
    thresholds = [0, -.0025, -.005, -.01, -.015, -.02]
    for predictor in market_predictors:
        for threshold in thresholds:
            mask = frame[predictor] <= threshold
            rows.append({"predictor": predictor, "condition": f"le_{threshold:g}",
                         **summarize(frame.loc[mask, "cycle_net_pnl"])})
    for threshold in (.3, .4, .5):
        mask = frame.constituent_gap_positive_fraction <= threshold
        rows.append({"predictor": "constituent_gap_positive_fraction", "condition": f"le_{threshold:g}",
                     **summarize(frame.loc[mask, "cycle_net_pnl"])})
    result = pd.DataFrame(rows)
    stability = []
    key_rules = {
        "QQQ_gap_below_zero": frame.QQQ_gap < 0,
        "QQQ_gap_le_minus_0p5pct": frame.QQQ_gap <= -.005,
        "QQQ_to_0940_below_zero": frame.QQQ_prior_close_to_0940 < 0,
        "QQQ_first10m_below_zero": frame.QQQ_open_to_0940 < 0,
        "breadth_below_half": frame.constituent_gap_positive_fraction < .5,
    }
    for rule, mask in key_rules.items():
        for period, pmask in {
            "2020_2022": frame.execution_date < "2023-01-01",
            "2023_2026": frame.execution_date >= "2023-01-01",
        }.items():
            for state in (True, False):
                z = frame.loc[pmask & mask.eq(state), "cycle_net_pnl"]
                stability.append({"rule": rule, "period": period, "weak_state": state, **summarize(z)})
    corr = {p: float(frame[[p, "cycle_net_pnl"]].corr().iloc[0, 1]) for p in market_predictors + ["constituent_gap_positive_fraction"]}
    return result, pd.DataFrame(stability), corr


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    frame = cycles()
    conditions, stability, corr = analyze(frame)
    frame.to_csv(OUT / "cycles_with_market_state.csv", index=False)
    conditions.to_csv(OUT / "conditional_results.csv", index=False)
    stability.to_csv(OUT / "chronological_stability.csv", index=False)
    report = {
        "status": "completed", "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0,
        "cycles": len(frame), "baseline_additive_return": float(frame.cycle_net_pnl.sum()),
        "correlations": corr,
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(conditions[conditions.predictor.isin(["all", "QQQ_gap", "QQQ_open_to_0940", "QQQ_prior_close_to_0940", "constituent_gap_positive_fraction"])].to_string(index=False))


if __name__ == "__main__":
    main()
