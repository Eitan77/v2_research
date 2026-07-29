from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import exchange_calendars as xcals
import pandas as pd

from cam0003 import max_drawdown_and_recovery, net_return, validate_cutoff


START = pd.Timestamp("2024-08-01")
CUTOFF = pd.Timestamp("2026-04-30")
EVAL_START = pd.Timestamp("2024-11-01")
SYMBOLS = ["EEM", "FXI", "EFA", "VWO", "IYR"]
MORNINGS = [15, 30, 60]
HOLDS = [15, 30, 60]
COMPONENTS = ["combined_previous_close", "open_session"]
STATES = ["all", "high_opening_volume", "high_opening_volatility"]


def schedule() -> pd.DataFrame:
    cal = xcals.get_calendar("XNYS")
    rows = []
    for session in cal.sessions_in_range(START, CUTOFF):
        op, cl = cal.session_open(session), cal.session_close(session)
        rows.append(
            {
                "date": session.tz_localize(None),
                "market_open": op,
                "expected_minutes": int((cl - op).total_seconds() // 60),
            }
        )
    return pd.DataFrame(rows)


def extract(input_path: Path, temp: Path) -> pd.DataFrame:
    sched = schedule()
    symbols = pd.DataFrame({"symbol": SYMBOLS})
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{str(temp).replace(chr(92), '/')}'")
    con.register("schedule", sched)
    con.register("symbols", symbols)
    q = """
    WITH raw AS (
      SELECT symbol,try_cast(timestamp AS TIMESTAMPTZ) ts,open,close,volume
      FROM read_parquet(?)
      WHERE symbol IN (SELECT symbol FROM symbols)
        AND timestamp < TIMESTAMPTZ '2026-05-01 00:00:00+00'
    ), grid AS (
      SELECT y.symbol,s.date,s.expected_minutes,r.i AS minute_index,
             s.market_open+r.i*INTERVAL 1 MINUTE AS ts
      FROM symbols y CROSS JOIN schedule s,
           range(0,s.expected_minutes) r(i)
    ), joined AS (
      SELECT g.*,b.open AS raw_open,b.close AS raw_close,b.volume AS raw_volume
      FROM grid g LEFT JOIN raw b USING(symbol,ts)
    ), filled AS (
      SELECT *,
        last_value(raw_close IGNORE NULLS) OVER (
          PARTITION BY symbol,date ORDER BY minute_index
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS price
      FROM joined
    ), rets AS (
      SELECT *,price/lag(price) OVER (
        PARTITION BY symbol,date ORDER BY minute_index
      )-1 AS minute_return
      FROM filled
    )
    SELECT symbol,date,max(expected_minutes) expected_minutes,
      count(raw_close) observed_minutes,
      max(CASE WHEN minute_index=0 THEN coalesce(raw_open,price) END) open_0930,
      max(CASE WHEN minute_index=14 THEN price END) close_m15,
      max(CASE WHEN minute_index=29 THEN price END) close_m30,
      max(CASE WHEN minute_index=59 THEN price END) close_m60,
      max(CASE WHEN minute_index=expected_minutes-15 THEN coalesce(raw_open,price) END) entry_h15,
      max(CASE WHEN minute_index=expected_minutes-30 THEN coalesce(raw_open,price) END) entry_h30,
      max(CASE WHEN minute_index=expected_minutes-60 THEN coalesce(raw_open,price) END) entry_h60,
      max(CASE WHEN minute_index=expected_minutes-1 THEN price END) session_close,
      sum(CASE WHEN minute_index<15 THEN coalesce(raw_volume,0) ELSE 0 END) volume_m15,
      sum(CASE WHEN minute_index<30 THEN coalesce(raw_volume,0) ELSE 0 END) volume_m30,
      sum(CASE WHEN minute_index<60 THEN coalesce(raw_volume,0) ELSE 0 END) volume_m60,
      sqrt(sum(CASE WHEN minute_index<15 THEN coalesce(minute_return*minute_return,0) ELSE 0 END)) rv_m15,
      sqrt(sum(CASE WHEN minute_index<30 THEN coalesce(minute_return*minute_return,0) ELSE 0 END)) rv_m30,
      sqrt(sum(CASE WHEN minute_index<60 THEN coalesce(minute_return*minute_return,0) ELSE 0 END)) rv_m60
    FROM rets GROUP BY symbol,date ORDER BY symbol,date
    """
    d = con.execute(q, [str(input_path)]).fetchdf()
    con.close()
    d["date"] = pd.to_datetime(d["date"])
    validate_cutoff(d)
    d["previous_close"] = d.groupby("symbol")["session_close"].shift(1)
    for morning in MORNINGS:
        for kind, col in [("volume", f"volume_m{morning}"), ("rv", f"rv_m{morning}")]:
            d[f"prior60_{kind}_q67_m{morning}"] = d.groupby("symbol")[col].transform(
                lambda s: s.shift(1).rolling(60, min_periods=60).quantile(2 / 3)
            )
    return d


def summarize(d: pd.DataFrame, symbol: str, component: str, morning: int, hold: int, state: str) -> dict:
    x = d[d["symbol"] == symbol].copy()
    morning_close = x[f"close_m{morning}"]
    signal_return = (
        morning_close / x["previous_close"] - 1
        if component == "combined_previous_close"
        else morning_close / x["open_0930"] - 1
    )
    active = signal_return > 0
    if state == "high_opening_volume":
        active &= x[f"volume_m{morning}"] > x[f"prior60_volume_q67_m{morning}"]
    elif state == "high_opening_volatility":
        active &= x[f"rv_m{morning}"] > x[f"prior60_rv_q67_m{morning}"]
    pnl = pd.Series(0.0, index=x.index)
    pnl.loc[active] = [
        net_return(a, b, 5.0)
        for a, b in zip(x.loc[active, f"entry_h{hold}"], x.loc[active, "session_close"])
    ]
    calendar = pd.DataFrame({"date": pd.date_range(EVAL_START, CUTOFF, freq="D")})
    calendar["net_pnl"] = calendar["date"].map(dict(zip(x["date"], pnl))).fillna(0.0)
    dd, recovery, unresolved = max_drawdown_and_recovery(calendar)
    monthly = calendar.assign(month=calendar["date"].dt.to_period("M")).groupby("month")["net_pnl"].sum()
    row = {
        "symbol": symbol,
        "component": component,
        "morning": morning,
        "hold": hold,
        "state": state,
        "cost": 5.0,
        "signal_days": int(active.sum()),
        "net": float(pnl.sum()),
        "positive_fraction": float((pnl.loc[active] > 0).mean()),
        "max_drawdown": dd,
        "recovery_days": recovery,
        "unresolved": unresolved,
    }
    for label, start in [("18m", "2024-11-01"), ("15m", "2025-02-01"), ("12m", "2025-05-01")]:
        m = monthly[monthly.index >= pd.Period(start, "M")]
        row.update(
            {
                f"{label}_net": float(m.sum()),
                f"{label}_avg_month": float(m.mean()),
                f"{label}_median_month": float(m.median()),
                f"{label}_negative_months": int((m < 0).sum()),
                f"{label}_zero_months": int((m == 0).sum()),
                f"{label}_trades": int((active & x["date"].ge(start)).sum()),
            }
        )
    return row


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    temp = a.output_dir / "duckdb_tmp"
    temp.mkdir(exist_ok=True)
    d = extract(a.input, temp)
    eval_ = d[d["date"] >= EVAL_START].copy()
    required = [
        "previous_close", "open_0930", "close_m15", "close_m30", "close_m60",
        "entry_h15", "entry_h30", "entry_h60", "session_close",
    ]
    nulls = {c: int(eval_[c].isna().sum()) for c in required}
    if any(nulls.values()):
        raise RuntimeError(f"price attrition {nulls}")
    eval_.to_parquet(a.output_dir / "source_etf_daily_features.parquet", index=False)
    rows = [
        summarize(eval_, symbol, component, morning, hold, state)
        for symbol in SYMBOLS
        for component in COMPONENTS
        for morning in MORNINGS
        for hold in HOLDS
        for state in STATES
    ]
    if len(rows) != 270:
        raise RuntimeError("variant mismatch")
    grid = pd.DataFrame(rows).sort_values("15m_avg_month", ascending=False)
    grid.to_csv(a.output_dir / "source_etf_grid.csv", index=False)
    diagnostics = {
        "rows": len(eval_),
        "symbols": SYMBOLS,
        "price_nulls": nulls,
        "observed_minutes_by_symbol": eval_.groupby("symbol")["observed_minutes"].agg(["min", "median"]).to_dict("index"),
        "max_date": str(eval_["date"].max().date()),
        "positive_variants": int((grid["net"] > 0).sum()),
        "positive_12m_variants": int((grid["12m_net"] > 0).sum()),
        "leaders": grid.head(30).to_dict(orient="records"),
    }
    (a.output_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    contract = {
        "executed_variant_count": 270,
        "expected_variant_count": 270,
        "symbols": SYMBOLS,
        "components": COMPONENTS,
        "mornings": MORNINGS,
        "holds": HOLDS,
        "states": STATES,
        "cost_bps_per_side": 5,
        "loaded_max_date": str(eval_["date"].max().date()),
        "holdout_rows_loaded": int((eval_["date"] >= "2026-05-01").sum()),
    }
    (a.output_dir / "contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(grid.head(40).to_string(index=False))


if __name__ == "__main__":
    main()
