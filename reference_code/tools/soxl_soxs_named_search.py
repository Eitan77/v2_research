"""Bounded, named SOXL/SOXS intraday hypothesis search.

Only SOXL and SOXS may be traded. All signals are generated from completed
5-minute bars through 2026-05-31; positions are entered on the next bar and
closed intraday. This is a research screen, not a live trading system.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


CUTOFF = pd.Timestamp("2026-06-01", tz="UTC")
TRADABLE = {"SOXL", "SOXS"}


def load_data(catalog: Path) -> pd.DataFrame:
    cols = [
        "timestamp", "session_date", "symbol", "open", "high", "low", "close", "volume", "vwap",
        "relative_volume_20", "atr_pct_14", "rsi_14", "bb_percent_b_20_2", "close_vs_ema_20",
        "close_vs_sma_20", "macd_hist_12_26_9", "adx_14", "upper_wick_pct", "lower_wick_pct",
    ]
    con = duckdb.connect(str(catalog), read_only=True)
    try:
        df = con.execute(
            f"select {', '.join(cols)} from technical_indicators where timeframe='5m' and symbol in ('SOXL','SOXS') and timestamp < '2026-06-01' order by timestamp, symbol"
        ).fetchdf()
    finally:
        con.close()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["ny_ts"] = df["timestamp"].dt.tz_convert("America/New_York")
    df["date"] = df["ny_ts"].dt.date.astype(str)
    df["minute"] = df["ny_ts"].dt.hour * 60 + df["ny_ts"].dt.minute
    df = df[(df["minute"] >= 570) & (df["minute"] < 960)].copy()
    for c in cols[3:]:
        if c not in {"symbol", "session_date"}:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["dollar_volume"] = df["close"] * df["volume"]
    df["session_vwap"] = df.groupby(["symbol", "date"], sort=False)["dollar_volume"].cumsum() / df.groupby(["symbol", "date"], sort=False)["volume"].cumsum()
    df["ret_1"] = df.groupby("symbol")["close"].pct_change(1)
    df["ret_3"] = df.groupby("symbol")["close"].pct_change(3)
    df["ret_6"] = df.groupby("symbol")["close"].pct_change(6)
    df["prev_close"] = df.groupby("symbol")["close"].shift(1)
    df["prev_vwap"] = df.groupby(["symbol", "date"])["session_vwap"].shift(1)
    df["atr_abs"] = df["close"] * df["atr_pct_14"]
    pair = df.pivot(index="timestamp", columns="symbol", values=["close", "ret_3", "ret_6", "session_vwap"]).sort_index()
    pair.columns = ["_".join(x) for x in pair.columns]
    pair["pair_ret_3"] = pair.get("ret_3_SOXL", 0.0) - pair.get("ret_3_SOXS", 0.0)
    pair["pair_ret_6"] = pair.get("ret_6_SOXL", 0.0) - pair.get("ret_6_SOXS", 0.0)
    pair["pair_z_3"] = (pair["pair_ret_3"] - pair["pair_ret_3"].rolling(48, min_periods=24).mean()) / pair["pair_ret_3"].rolling(48, min_periods=24).std()
    pair["pair_z_6"] = (pair["pair_ret_6"] - pair["pair_ret_6"].rolling(48, min_periods=24).mean()) / pair["pair_ret_6"].rolling(48, min_periods=24).std()
    pair = pair.reset_index()[["timestamp", "pair_ret_3", "pair_ret_6", "pair_z_3", "pair_z_6"]]
    df = df.merge(pair, on="timestamp", how="left")
    # Opening-range levels are built only from completed bars.
    orb = df[df["minute"] < 600].groupby("date").agg(orb_high=("high", "max"), orb_low=("low", "min")).reset_index()
    df = df.merge(orb, on="date", how="left")
    return df.sort_values(["timestamp", "symbol"]).reset_index(drop=True)


def specs() -> list[dict]:
    out = []
    for hold in [1, 2, 3, 6]:
        for target, stop in [(0.005, 0.004), (0.010, 0.006), (0.015, 0.008), (0.025, 0.012)]:
            out.append({"family": "vwap_momentum", "hold": hold, "target": target, "stop": stop})
            out.append({"family": "orb_momentum", "hold": hold, "target": target, "stop": stop})
            out.append({"family": "pair_reversion", "hold": hold, "target": target, "stop": stop})
            out.append({"family": "vwap_reversion", "hold": hold, "target": target, "stop": stop})
    return out


def signal_rows(df: pd.DataFrame, spec: dict) -> pd.DataFrame:
    fam = spec["family"]
    d = df.copy()
    after_open = d["minute"].between(600, 930)
    vwap_up = (d["close"] > d["session_vwap"]) & (d["prev_close"] <= d["prev_vwap"])
    vwap_down = (d["close"] < d["session_vwap"]) & (d["prev_close"] >= d["prev_vwap"])
    if fam == "vwap_momentum":
        long_signal = after_open & vwap_up & (d["ret_3"] > 0.004) & (d["relative_volume_20"] >= 1.2) & (d["close_vs_ema_20"] > 0)
    elif fam == "orb_momentum":
        long_signal = after_open & (d["close"] > d["orb_high"]) & (d["close"] > d["session_vwap"]) & (d["relative_volume_20"] >= 1.2) & (d["ret_3"] > 0)
    elif fam == "pair_reversion":
        # Buy the permitted ETF whose relative move is the oversold leg; no short leg is used.
        long_signal = after_open & (d["pair_z_3"].abs() >= 1.5) & (d["ret_3"] < -0.003) & (d["close"] > d["prev_close"]) & (d["relative_volume_20"] >= 1.0)
    elif fam == "vwap_reversion":
        long_signal = after_open & vwap_down & (d["bb_percent_b_20_2"] < 0.10) & (d["rsi_14"] < 38) & (d["relative_volume_20"] >= 1.0)
    else:
        raise ValueError(fam)
    sig = d.loc[long_signal, ["timestamp", "date", "symbol", "minute", "open", "high", "low", "close", "atr_abs", "relative_volume_20"]].copy()
    sig = sig.rename(columns={"timestamp": "signal_ts"})
    # Only one signal is allowed per timestamp; choose the stronger eligible leg.
    sig["strength"] = sig["close"].pct_change().abs().fillna(0) + sig["relative_volume_20"].fillna(0) * 0.0001
    sig = sig.sort_values(["signal_ts", "strength"], ascending=[True, False]).drop_duplicates("signal_ts")
    return sig.sort_values("signal_ts").reset_index(drop=True)


def attach_exits(signals: pd.DataFrame, bars: pd.DataFrame, spec: dict) -> pd.DataFrame:
    by_symbol = {s: g.sort_values("timestamp").reset_index(drop=True) for s, g in bars.groupby("symbol")}
    index_maps = {s: {ts: i for i, ts in enumerate(g["timestamp"])} for s, g in by_symbol.items()}
    rows = []
    for row in signals.itertuples(index=False):
        g = by_symbol[row.symbol]
        i = index_maps[row.symbol].get(row.signal_ts)
        if i is None or i + 1 >= len(g):
            continue
        entry = g.iloc[i + 1]
        entry_price = float(entry["open"])
        target = entry_price * (1.0 + spec["target"])
        stop = entry_price * (1.0 - spec["stop"])
        end = min(i + 1 + int(spec["hold"]), len(g) - 1)
        exit_row = g.iloc[end]
        exit_price = float(exit_row["close"])
        exit_ts = exit_row["timestamp"]
        reason = "time"
        for j in range(i + 1, end + 1):
            b = g.iloc[j]
            if float(b["low"]) <= stop:
                exit_price, exit_ts, reason = stop, b["timestamp"], "stop"
                break
            if float(b["high"]) >= target:
                exit_price, exit_ts, reason = target, b["timestamp"], "target"
                break
        rows.append({"signal_ts": row.signal_ts, "entry_ts": entry["timestamp"], "exit_ts": exit_ts, "symbol": row.symbol, "entry_price": entry_price, "exit_price": exit_price, "gross_return": exit_price / entry_price - 1.0, "reason": reason, "entry_minute": int(entry["timestamp"].tz_convert("America/New_York").hour * 60 + entry["timestamp"].tz_convert("America/New_York").minute)})
    return pd.DataFrame(rows)


def simulate(trades: pd.DataFrame, cost: float, total_sample_days: int) -> tuple[dict, pd.DataFrame]:
    if trades.empty:
        return {"trades": 0, "mean_monthly_net_pct": 0.0}, trades
    t = trades.sort_values(["entry_ts", "exit_ts"]).copy()
    accepted = []
    next_free = pd.Timestamp.min.tz_localize("UTC")
    for row in t.itertuples(index=False):
        if row.entry_ts >= next_free:
            accepted.append(row._asdict())
            next_free = row.exit_ts
    e = pd.DataFrame(accepted)
    e["cost_bps_side"] = np.where(e["entry_minute"] < 600, 20.0, float(cost))
    e["net_return"] = e["gross_return"] - 2.0 * e["cost_bps_side"] / 10000.0
    e["ny_month"] = e["entry_ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m")
    e["year"] = e["entry_ts"].dt.year
    month = e.groupby("ny_month")["net_return"].apply(lambda x: float((1.0 + x).prod() - 1.0))
    equity = np.cumprod(np.clip(1.0 + e["net_return"].to_numpy(float), 1e-12, None))
    dd = equity / np.maximum.accumulate(equity) - 1.0
    years = e.groupby("year")["net_return"].apply(lambda x: float((1.0 + x).prod() - 1.0))
    days = max(int(total_sample_days), 1)
    daily = e.groupby(e["entry_ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d"))["net_return"].apply(lambda x: float((1.0 + x).prod() - 1.0))
    metrics = {
        "trades": int(len(e)), "sample_days": int(days), "trades_per_trading_day": float(len(e) / max(days, 1)),
        "mean_trade_net_pct": float(e["net_return"].mean() * 100), "mean_active_day_net_pct": float(daily.mean() * 100) if len(daily) else 0.0,
        "median_active_day_net_pct": float(daily.median() * 100) if len(daily) else 0.0, "mean_monthly_net_pct": float(month.mean() * 100) if len(month) else 0.0,
        "median_monthly_net_pct": float(month.median() * 100) if len(month) else 0.0, "worst_month_net_pct": float(month.min() * 100) if len(month) else 0.0,
        "max_drawdown": float(dd.min()) if len(dd) else 0.0, "years_tested": int(len(years)), "positive_years": int((years > 0).sum()),
        "worst_year_return": float(years.min()) if len(years) else 0.0, "win_rate": float((e["net_return"] > 0).mean()) if len(e) else 0.0,
    }
    metrics["frequency_gate"] = bool(metrics["trades_per_trading_day"] >= 0.5)
    metrics["hard_gate"] = bool(metrics["frequency_gate"] and metrics["mean_monthly_net_pct"] >= 10.0 and metrics["years_tested"] >= 2 and metrics["max_drawdown"] >= -0.35 and metrics["worst_year_return"] >= -0.35)
    return metrics, e


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="D:/AlgoResearch/data/catalog.duckdb", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    bars = load_data(args.catalog)
    all_metrics, all_trades = [], []
    for spec_id, spec in enumerate(specs()):
        sig = signal_rows(bars, spec)
        trades = attach_exits(sig, bars, spec)
        for cost in [0, 5, 10, 15, 20]:
            m, e = simulate(trades, cost, bars["date"].nunique())
            m.update({"spec_id": spec_id, **spec, "cost_bps_later_day": cost})
            all_metrics.append(m)
            if m.get("hard_gate"):
                e["spec_id"] = spec_id
                e["family"] = spec["family"]
                e["cost_bps_later_day"] = cost
                all_trades.append(e)
    metrics = pd.DataFrame(all_metrics).sort_values(["hard_gate", "mean_monthly_net_pct", "mean_active_day_net_pct"], ascending=False)
    metrics.to_csv(args.out / "soxl_soxs_named_metrics.csv", index=False)
    if all_trades:
        pd.concat(all_trades, ignore_index=True).to_parquet(args.out / "soxl_soxs_hard_gate_trades.parquet", index=False)
    top = metrics.groupby("cost_bps_later_day", as_index=False).head(5)
    top.to_csv(args.out / "soxl_soxs_top_by_cost.csv", index=False)
    summary = {"rows": int(len(bars)), "symbols": sorted(bars.symbol.unique().tolist()), "specs": len(specs()), "metric_rows": len(metrics), "hard_gate_rows": int(metrics["hard_gate"].sum()), "max_mean_monthly_net_pct": float(metrics["mean_monthly_net_pct"].max()), "max_mean_active_day_net_pct": float(metrics["mean_active_day_net_pct"].max()), "cutoff_exclusive": "2026-06-01", "quote_path_eligible": bool(metrics["hard_gate"].any())}
    (args.out / "soxl_soxs_named_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    lines = ["# SOXL/SOXS named hypothesis search", "", f"Rows: {len(bars):,}; tradable symbols: {sorted(bars.symbol.unique().tolist())}; formula-free named specs: {len(specs())}; cutoff: 2026-06-01 exclusive.", "", f"Hard-gate rows: {int(metrics['hard_gate'].sum())}; quote-path eligible: {bool(metrics['hard_gate'].any())}.", "", "## Top candidates by later-day cost", "", "```text", top.to_string(index=False), "```", "", "Opening entries were charged 20 bps/side; later entries use the stated cost tier. This is a bar-level screen; quote-path fills remain mandatory before promotion."]
    (args.out / "soxl_soxs_named_search.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
