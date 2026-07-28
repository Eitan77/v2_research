"""Capital-aware Terra candidate audit.

This deliberately ranks nothing by raw signal compounding. It selects one
non-overlapping position at a time, applies a cost grid to gross returns, and
reports monthly, yearly, and QQQ market-regime results through the discovery
cutoff only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


CUTOFF = pd.Timestamp("2026-06-01", tz="UTC")


def _qqq_regimes(catalog: Path) -> pd.DataFrame:
    con = duckdb.connect(str(catalog), read_only=True)
    try:
        q = con.execute(
            """
            select timestamp, close
            from bars_1d
            where symbol = 'QQQ' and timestamp < '2026-06-01'
            order by timestamp
            """
        ).fetchdf()
    finally:
        con.close()
    if q.empty:
        return pd.DataFrame(columns=["date", "regime"])
    q["date"] = pd.to_datetime(q["timestamp"], utc=True).dt.normalize()
    q["close"] = pd.to_numeric(q["close"], errors="coerce")
    q = q.dropna(subset=["date", "close"]).drop_duplicates("date").sort_values("date")
    q["sma200"] = q["close"].rolling(200, min_periods=50).mean()
    q["ret1"] = q["close"].pct_change()
    q["vol20"] = q["ret1"].rolling(20, min_periods=10).std() * np.sqrt(252.0)
    vol_mid = float(q["vol20"].median()) if q["vol20"].notna().any() else 0.0
    q["trend"] = np.where(q["close"] >= q["sma200"], "bull", "bear")
    q["vol_state"] = np.where(q["vol20"] >= vol_mid, "high_vol", "low_vol")
    q["regime"] = q["trend"] + "_" + q["vol_state"]
    return q[["date", "regime"]]


def _select_nonoverlap(group: pd.DataFrame) -> pd.DataFrame:
    rows = []
    next_free = pd.Timestamp.min.tz_localize("UTC")
    for row in group.sort_values(["entry_ts", "exit_ts"]).itertuples(index=False):
        if row.entry_ts >= next_free:
            rows.append(row._asdict())
            next_free = row.exit_ts
    return pd.DataFrame(rows)


def _audit_candidate(group: pd.DataFrame, costs: list[float], regimes: pd.DataFrame, target: float) -> tuple[list[dict], list[dict]]:
    events = _select_nonoverlap(group)
    if events.empty:
        return [], []
    events["entry_ts"] = pd.to_datetime(events["entry_ts"], utc=True)
    events["exit_ts"] = pd.to_datetime(events["exit_ts"], utc=True)
    events = events[events["entry_ts"] < CUTOFF].copy()
    if events.empty:
        return [], []
    events["ny_month"] = events["entry_ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m")
    events["year"] = events["entry_ts"].dt.year
    events["regime_date"] = events["entry_ts"].dt.normalize()
    if not regimes.empty:
        events = pd.merge_asof(events.sort_values("regime_date"), regimes.sort_values("date"), left_on="regime_date", right_on="date", direction="backward")
    else:
        events["regime"] = "unknown"
    events["regime"] = events["regime"].fillna("unknown")
    gross = pd.to_numeric(events["gross_source_return"], errors="coerce").to_numpy(float)
    out, monthly_rows = [], []
    start_month = events["ny_month"].min()
    end_month = events["ny_month"].max()
    month_index = pd.period_range(start_month, end_month, freq="M").astype(str)
    for cost in costs:
        net = gross - 2.0 * float(cost) / 10000.0
        events["net_return"] = net
        equity = np.cumprod(np.clip(1.0 + net, 1e-12, None))
        peak = np.maximum.accumulate(equity)
        drawdown = equity / np.maximum(peak, 1e-12) - 1.0
        month_values = events.groupby("ny_month", sort=True)["net_return"].agg(lambda x: float((1.0 + x).prod() - 1.0)).to_dict()
        month_simple = events.groupby("ny_month", sort=True)["net_return"].sum().to_dict()
        monthly = pd.DataFrame({"month": month_index})
        monthly["monthly_compounded_return"] = monthly["month"].map(month_values).fillna(0.0)
        monthly["monthly_simple_return"] = monthly["month"].map(month_simple).fillna(0.0)
        monthly["candidate_id"] = str(group["candidate_id"].iloc[0])
        monthly["cost_bps_per_side"] = cost
        monthly_rows.extend(monthly.to_dict("records"))
        years = events.groupby("year")["net_return"].agg(lambda x: float((1.0 + x).prod() - 1.0))
        regime_stats = events.groupby("regime")["net_return"].agg(["count", "sum", "mean"])
        regime_min = float(regime_stats["sum"].min()) if not regime_stats.empty else 0.0
        row = {
            "candidate_id": str(group["candidate_id"].iloc[0]),
            "cost_bps_per_side": float(cost),
            "events": int(len(events)),
            "calendar_months": int(len(monthly)),
            "active_months": int((monthly["monthly_compounded_return"] != 0).sum()),
            "mean_monthly_net_pct": float(monthly["monthly_compounded_return"].mean() * 100.0),
            "median_monthly_net_pct": float(monthly["monthly_compounded_return"].median() * 100.0),
            "worst_month_net_pct": float(monthly["monthly_compounded_return"].min() * 100.0),
            "simple_total_return": float(net.sum()),
            "compounded_total_return": float(equity[-1] - 1.0),
            "max_drawdown": float(drawdown.min()),
            "years_tested": int(len(years)),
            "positive_years": int((years > 0).sum()),
            "worst_year_return": float(years.min()) if len(years) else 0.0,
            "regimes_tested": int(len(regime_stats)),
            "worst_regime_simple_return": regime_min,
            "long_only": bool((group["side"].astype(str).str.lower() == "long").all()),
            "no_overlap": True,
        }
        row["hard_gate_pass"] = bool(
            row["long_only"]
            and row["no_overlap"]
            and row["calendar_months"] >= 24
            and row["mean_monthly_net_pct"] >= target
            and row["max_drawdown"] >= -0.35
            and row["worst_year_return"] >= -0.35
            and row["worst_regime_simple_return"] >= -0.35
        )
        out.append(row)
    return out, monthly_rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True, type=Path)
    ap.add_argument("--catalog", default="D:/AlgoResearch/data/catalog.duckdb", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--costs", nargs="+", type=float, default=[0, 2, 5, 10, 25, 50])
    ap.add_argument("--target-monthly-pct", type=float, default=10.0)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    parquet = str(args.trades)
    con = duckdb.connect()
    try:
        schema = con.execute("describe select * from parquet_scan(?)", [parquet]).fetchdf()
        required = {"candidate_id", "entry_ts", "exit_ts", "side", "gross_source_return"}
        missing = required - set(schema["column_name"].astype(str))
        if missing:
            raise ValueError(f"trade ledger missing columns: {sorted(missing)}")
        global_checks = con.execute(
            """
            select
              coalesce(bool_or(entry_ts >= timestamptz '2026-06-01 00:00:00+00'), false) as bad_entry,
              coalesce(bool_or(exit_ts >= timestamptz '2026-06-01 00:00:00+00'), false) as bad_exit,
              coalesce(bool_or(lower(cast(side as varchar)) <> 'long'), false) as bad_side
            from parquet_scan(?)
            """,
            [parquet],
        ).fetchone()
        if any(bool(x) for x in global_checks):
            raise RuntimeError(f"sealed holdout contamination or non-long trade detected: {global_checks}")
        candidate_ids = [row[0] for row in con.execute("select distinct candidate_id from parquet_scan(?) order by candidate_id", [parquet]).fetchall()]
    finally:
        con.close()
    regimes = _qqq_regimes(args.catalog)
    metrics, monthly = [], []
    stream_con = duckdb.connect()
    try:
      for candidate_id in candidate_ids:
        group = stream_con.execute(
            """
            select candidate_id, entry_ts, exit_ts, side, gross_source_return
            from parquet_scan(?)
            where candidate_id = ?
            order by entry_ts, exit_ts
            """,
            [parquet, candidate_id],
        ).fetchdf()
        m, mo = _audit_candidate(group, args.costs, regimes, args.target_monthly_pct)
        metrics.extend(m)
        monthly.extend(mo)
    finally:
        stream_con.close()
    metrics_df = pd.DataFrame(metrics).sort_values(["hard_gate_pass", "mean_monthly_net_pct"], ascending=[False, False]) if metrics else pd.DataFrame()
    monthly_df = pd.DataFrame(monthly)
    metrics_df.to_csv(args.out / "terra_hard_gate_metrics.csv", index=False)
    monthly_df.to_csv(args.out / "terra_hard_gate_monthly.csv", index=False)
    summary = {
        "trades_path": str(args.trades),
        "cutoff_exclusive": CUTOFF.isoformat(),
        "costs_bps_per_side": args.costs,
        "target_monthly_net_pct": args.target_monthly_pct,
        "candidate_count": int(metrics_df["candidate_id"].nunique()) if not metrics_df.empty else 0,
        "hard_gate_rows": int(metrics_df["hard_gate_pass"].sum()) if not metrics_df.empty else 0,
        "hard_gate_candidates_at_all_costs": int(metrics_df.groupby("candidate_id")["hard_gate_pass"].all().sum()) if not metrics_df.empty else 0,
    }
    (args.out / "terra_hard_gate_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
