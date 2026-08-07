from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


WORKSPACE = Path(__file__).resolve().parents[3]
CAMPAIGN = WORKSPACE / "campaigns" / "CAM-0607"
CATALOG = Path(r"D:\AlgoResearch\data\catalog.duckdb")
ETF_SYMBOLS = (
    "ARKK", "DIA", "GLD", "HYG", "IWM", "QQQ", "SMH", "SOXL", "SOXS", "SPY",
    "SQQQ", "TLT", "TQQQ", "USO", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY",
)
COSTS = (-1.0, 0.0, 1.0, 2.0, 5.0, 10.0)


def load(timeframe: str) -> pd.DataFrame:
    table = f"derived_bars_{timeframe}"
    symbols_sql = ",".join(f"'{x}'" for x in ETF_SYMBOLS)
    with duckdb.connect(str(CATALOG), read_only=True) as con:
        con.execute("SET threads=16")
        frame = con.execute(f"""
            SELECT symbol, timestamp, try_cast(session_date AS DATE) AS session_date, open, high, low, close
            FROM {table}
            WHERE symbol IN ({symbols_sql})
              AND feed='sip'
              AND adjustment='raw'
              AND try_cast(session_date AS DATE) >= DATE '2021-05-03'
              AND try_cast(session_date AS DATE) <= DATE '2026-04-30'
            ORDER BY symbol, timestamp
        """).df()
    if frame.empty:
        raise RuntimeError(f"no {timeframe} ETF bars")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["session_date"] = pd.to_datetime(frame["session_date"])
    if (frame["session_date"] >= pd.Timestamp("2026-05-01")).any():
        raise RuntimeError("intraday load crossed holdout")
    if frame.duplicated(["symbol", "timestamp"]).any():
        raise RuntimeError("duplicate intraday symbol timestamps")
    group = frame.groupby(["symbol", "session_date"], sort=False)
    for field in ("open", "high", "low", "close"):
        frame[f"next_{field}"] = group[field].shift(-1)
    frame["next_timestamp"] = group["timestamp"].shift(-1)
    return frame


def maximum_drawdown(net: pd.Series) -> float:
    equity = 1.0 + net.cumsum()
    peak = equity.cummax()
    return float(((peak-equity)/peak).max())


def main() -> None:
    output = CAMPAIGN / "artifacts" / "RUN-0004"
    output.mkdir(parents=True, exist_ok=True)
    records = []
    best_daily = None
    best_key = None
    best_net = -np.inf
    source_reports = {}
    for timeframe in ("5m", "15m"):
        frame = load(timeframe)
        source_reports[timeframe] = {
            "rows": int(len(frame)), "symbols": int(frame["symbol"].nunique()),
            "minimum_timestamp": frame["timestamp"].min().isoformat(),
            "maximum_timestamp": frame["timestamp"].max().isoformat(), "holdout_rows_loaded": 0,
        }
        grouped = frame.groupby(["symbol", "session_date"], sort=False)["close"]
        for lookback in (1, 3, 6):
            prior = grouped.shift(lookback)
            ret = frame["close"] / prior - 1.0
            timestamp_group = ret.groupby(frame["timestamp"])
            residual = ret - timestamp_group.transform("mean")
            sigma = residual.groupby(frame["timestamp"]).transform("std")
            zscore = residual / sigma
            for threshold in (0.0, 0.5, 1.0):
                base_raw = -residual.where(zscore.abs() >= threshold)
                for mode in ("long", "long_short"):
                    raw = base_raw.clip(lower=0.0) if mode == "long" else base_raw
                    denominator = raw.abs().groupby(frame["timestamp"]).transform("sum")
                    weight = (raw / denominator).where(denominator > 0, 0.0).fillna(0.0)
                    next_valid = frame[["next_open", "next_high", "next_low", "next_close"]].notna().all(axis=1)
                    weight = weight.where(next_valid, 0.0)
                    for stop in (0.005, 0.01, 0.02):
                        long_return = np.where(
                            frame["next_low"] <= frame["next_open"]*(1.0-stop),
                            -stop,
                            frame["next_close"]/frame["next_open"]-1.0,
                        )
                        short_asset_return = np.where(
                            frame["next_high"] >= frame["next_open"]*(1.0+stop),
                            stop,
                            frame["next_close"]/frame["next_open"]-1.0,
                        )
                        asset_return = np.where(weight >= 0, long_return, short_asset_return)
                        gross_component = weight.to_numpy(float) * np.nan_to_num(asset_return, nan=0.0)
                        base_turnover = 2.0 * weight.abs().to_numpy(float)
                        for cost in COSTS:
                            component = gross_component - base_turnover*cost/10000.0
                            session = pd.Series(component).groupby(frame["session_date"].to_numpy()).sum()
                            monthly = session.groupby(session.index.to_period("M")).sum()
                            key = f"{timeframe}__r{lookback}__z{threshold:g}__{mode}__stop{stop:g}"
                            record = {
                                "variant_id": key, "timeframe": timeframe, "lookback_bars": lookback,
                                "z_threshold": threshold, "mode": mode, "protective_stop": stop,
                                "cost_bps_per_side": cost, "net_simple_return": float(session.sum()),
                                "gross_simple_return": float(gross_component.sum()),
                                "maximum_drawdown": maximum_drawdown(session),
                                "trades": int((weight.abs() > 1e-12).sum()),
                                "active_sessions": int((session.abs() > 1e-12).sum()),
                                "positive_months": int((monthly > 0).sum()),
                                "negative_months": int((monthly < 0).sum()),
                                "monthly_average": float(monthly.mean()),
                                "monthly_median": float(monthly.median()),
                                "recent12_average_month": float(monthly.iloc[-12:].mean()),
                                "forced_exit": "next_bar_close", "broker_margin": False,
                                "direct_short": mode == "long_short", "holdout_rows_loaded": 0,
                            }
                            records.append(record)
                            if cost == 2.0 and record["net_simple_return"] > best_net:
                                best_net = record["net_simple_return"]
                                best_key = key
                                best_daily = session.rename_axis("date").reset_index(name="net_pnl")
    metrics = pd.DataFrame(records).sort_values(["cost_bps_per_side", "net_simple_return"], ascending=[True, False])
    metrics.to_csv(output / "variant_metrics.csv", index=False)
    if best_daily is not None:
        best_daily.to_parquet(output / "best_2bps_daily.parquet", index=False)
    report = {
        "status": "completed", "campaign_id": "CAM-0607", "run_id": "RUN-0004",
        "generated_utc": datetime.now(timezone.utc).isoformat(), "source_reports": source_reports,
        "variant_cost_rows": int(len(metrics)), "best_2bps_variant": best_key,
        "best_2bps_net_return": float(best_net), "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0, "broker_margin": False,
        "short_safeguard": "predefined per-bar stop and forced next-bar-close exit",
        "quote_replay_required_for_any_survivor": True,
    }
    (output / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
