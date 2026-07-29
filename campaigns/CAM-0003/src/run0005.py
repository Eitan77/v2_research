from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cam0003 import max_drawdown_and_recovery, net_return, validate_cutoff
from readiness import paths, schedule


EVAL_START = pd.Timestamp("2024-11-01")
CUTOFF = pd.Timestamp("2026-04-30")


def closing_path(temp: Path) -> pd.DataFrame:
    sched = schedule()
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{str(temp).replace(chr(92), '/')}'")
    con.register("schedule", sched)
    q = """
    WITH ranked AS (
      SELECT b.*,
        row_number() OVER (
          PARTITION BY b.symbol,b.timestamp,b.timeframe,b.feed,b.adjustment
          ORDER BY coalesce(try_cast(b.ingested_at AS TIMESTAMP),TIMESTAMP '1900-01-01') DESC,
                   coalesce(b.source_ingestion_id,'') DESC
        ) rn
      FROM read_parquet(?,union_by_name=true,hive_partitioning=true) b
      WHERE b.date BETWEEN DATE '2024-10-01' AND DATE '2026-04-30'
        AND b.symbol='SPY' AND b.feed='sip' AND b.adjustment='raw'
    )
    SELECT s.date,try_cast(b.timestamp AS TIMESTAMPTZ) ts,b.open,b.high,b.close
    FROM ranked b JOIN schedule s ON b.date=s.date
    WHERE b.rn=1
      AND try_cast(b.timestamp AS TIMESTAMPTZ)>=s.market_close-INTERVAL 30 MINUTE
      AND try_cast(b.timestamp AS TIMESTAMPTZ)<s.market_close
    ORDER BY s.date,ts
    """
    d = con.execute(q, [paths()]).fetchdf()
    con.close()
    d["date"] = pd.to_datetime(d["date"])
    validate_cutoff(d)
    counts = d.groupby("date").size()
    if (counts < 30).any():
        raise RuntimeError(f"incomplete closing paths: {counts[counts < 30].to_dict()}")
    return d


def short_pnl(path: pd.DataFrame, stop: float) -> tuple[pd.Series, pd.Series]:
    pnl = {}
    stopped = {}
    for date, x in path.groupby("date", sort=True):
        x = x.sort_values("ts")
        entry = float(x.iloc[0]["open"])
        stop_price = entry * (1 + stop)
        touches = x[x["high"] >= stop_price]
        if len(touches):
            first = touches.iloc[0]
            fill = max(stop_price, float(first["open"])) * 1.0002
            stopped[date] = True
        else:
            fill = float(x.iloc[-1]["close"])
            stopped[date] = False
        pnl[date] = (entry - fill) / entry - 0.0004
    return pd.Series(pnl), pd.Series(stopped)


def summary(name: str, dates: pd.Series, pnl: pd.Series, active: pd.Series, extra: dict) -> dict:
    trade_pnl = pd.Series(dates.map(pnl).to_numpy(), index=dates.index).where(active, 0.0)
    calendar = pd.DataFrame({"date": pd.date_range(EVAL_START, CUTOFF, freq="D")})
    calendar["net_pnl"] = calendar["date"].map(dict(zip(dates, trade_pnl))).fillna(0.0)
    dd, recovery, unresolved = max_drawdown_and_recovery(calendar)
    monthly = calendar.assign(month=calendar["date"].dt.to_period("M")).groupby("month")["net_pnl"].sum()
    row = {
        "variant": name,
        "trades": int(active.sum()),
        "net": float(trade_pnl.sum()),
        "positive_fraction": float((trade_pnl[active] > 0).mean()),
        "max_drawdown": dd,
        "recovery_days": recovery,
        "unresolved": unresolved,
        **extra,
    }
    for label, start in [("18m", "2024-11-01"), ("15m", "2025-02-01"), ("12m", "2025-05-01")]:
        m = monthly[monthly.index >= pd.Period(start, "M")]
        selected = active & dates.ge(start)
        row.update(
            {
                f"{label}_net": float(m.sum()),
                f"{label}_avg_month": float(m.mean()),
                f"{label}_median_month": float(m.median()),
                f"{label}_negative_months": int((m < 0).sum()),
                f"{label}_zero_months": int((m == 0).sum()),
                f"{label}_trades": int(selected.sum()),
            }
        )
    return row


def positive_rolling_quantile(s: pd.Series, q: float) -> pd.Series:
    def calc(v: np.ndarray) -> float:
        p = v[v > 0]
        return float(np.quantile(p, q)) if len(p) >= 15 else np.nan
    return s.shift(1).rolling(60, min_periods=30).apply(calc, raw=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    temp = a.output_dir / "duckdb_tmp"
    temp.mkdir(exist_ok=True)

    daily = pd.read_parquet("campaigns/CAM-0003/artifacts/RUN-0001/daily_signals.parquet")
    daily["date"] = pd.to_datetime(daily["date"])
    validate_cutoff(daily)
    path = closing_path(temp)
    if set(daily["date"]) - set(path["date"]):
        raise RuntimeError("closing path date attrition")

    results = []
    negative = daily["first_half_hour_return"] < 0
    for stop in [0.005, 0.01, 0.02]:
        pnl, stopped = short_pnl(path, stop)
        results.append(
            summary(
                f"negative_morning_short_stop_{stop:.3f}",
                daily["date"],
                pnl,
                negative,
                {
                    "side": "short",
                    "stop_pct": stop,
                    "stops_on_active_days": int(
                        sum(bool(stopped.get(d, False)) for d in daily.loc[negative, "date"])
                    ),
                },
            )
        )
        daily[f"short_pnl_stop_{stop:.3f}"] = daily["date"].map(pnl)

    gross_long = pd.Series(
        [
            net_return(entry, exit_, 2.0)
            for entry, exit_ in zip(daily["entry_1530"], daily["exit_1559"])
        ],
        index=daily.index,
    )
    states = {"all_positive": daily["first_half_hour_return"] > 0}
    for label, q in [("q50", 0.5), ("q667", 2 / 3), ("q80", 0.8)]:
        threshold = positive_rolling_quantile(daily["first_half_hour_return"], q)
        states[label] = daily["first_half_hour_return"] > threshold
        daily[f"positive_threshold_{label}"] = threshold
    pnl_map = pd.Series(gross_long.to_numpy(), index=daily["date"])
    for label, active in states.items():
        results.append(
            summary(
                f"positive_morning_long_{label}",
                daily["date"],
                pnl_map,
                active,
                {"side": "long", "stop_pct": None, "stops_on_active_days": None},
            )
        )

    if len(results) != 7:
        raise RuntimeError("variant count mismatch")
    out = pd.DataFrame(results).sort_values("15m_avg_month", ascending=False)
    daily.to_parquet(a.output_dir / "daily_path.parquet", index=False)
    out.to_csv(a.output_dir / "source_completion.csv", index=False)
    diagnostics = {
        "source_negative_signal_days": int(negative.sum()),
        "closing_path_rows": int(len(path)),
        "closing_path_sessions": int(path["date"].nunique()),
        "gross_mean_closing_return_negative_morning": float(
            daily.loc[negative, "last_half_hour_gross_return"].mean()
        ),
        "gross_hypothetical_unprotected_source_short_mean": float(
            -daily.loc[negative, "last_half_hour_gross_return"].mean()
        ),
        "positive_magnitude_monotonic_15m": out[out["side"] == "long"]
        .sort_values("trades", ascending=False)[["variant", "trades", "15m_avg_month"]]
        .to_dict("records"),
        "variants": out.to_dict("records"),
    }
    (a.output_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    contract = {
        "executed_variant_count": 7,
        "expected_variant_count": 7,
        "short_stops": [0.005, 0.01, 0.02],
        "short_forced_flat": True,
        "short_cost_bps_per_side": 2,
        "long_magnitude_states": list(states),
        "long_cost_bps_per_side": 2,
        "loaded_max_date": str(daily["date"].max().date()),
        "holdout_rows_loaded": int((daily["date"] >= "2026-05-01").sum()),
    }
    if contract["holdout_rows_loaded"]:
        raise RuntimeError("holdout contamination")
    (a.output_dir / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
