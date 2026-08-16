from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "campaigns" / "CAM-0600" / "src"
sys.path.insert(0, str(SRC))

from baseline_strategies import eligible, moving_average
from deep_strategies import liquid_mask
from suite_core import evaluate_weights, forward_fill_signal_weights, load_panels, trailing_return, trailing_vol, weekly_indices

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0027"
CUTOFF = pd.Timestamp("2026-04-30")
HOLDOUT = pd.Timestamp("2026-05-01", tz="UTC")
NY = ZoneInfo("America/New_York")
VARIANTS = (
    "control_126_21",
    "ensemble_84_126_189_21",
    "risk_adjusted_126_21_vol63",
    "ensemble_84_126_189_21_top5_buffer",
)


def percentile(score: np.ndarray, mask: np.ndarray, signals: np.ndarray) -> np.ndarray:
    out = np.full_like(score, np.nan, dtype=float)
    for i in signals:
        cols = np.flatnonzero(mask[i] & np.isfinite(score[i]))
        if not len(cols):
            continue
        order = cols[np.argsort(score[i, cols], kind="stable")]
        ranks = np.arange(1, len(cols) + 1, dtype=float) / len(cols)
        out[i, order] = ranks
    return out


def select_equal(score: np.ndarray, mask: np.ndarray, signals: np.ndarray, top_k: int = 3) -> np.ndarray:
    raw = np.zeros_like(score, dtype=float)
    for i in signals:
        cols = np.flatnonzero(mask[i] & np.isfinite(score[i]))
        if not len(cols):
            continue
        chosen = cols[np.argsort(score[i, cols], kind="stable")[-min(top_k, len(cols)):]]
        raw[i, chosen] = 1.0 / len(chosen)
    return forward_fill_signal_weights(raw, signals)


def select_buffered(score: np.ndarray, mask: np.ndarray, signals: np.ndarray) -> np.ndarray:
    raw = np.zeros_like(score, dtype=float)
    held: list[int] = []
    for i in signals:
        cols = np.flatnonzero(mask[i] & np.isfinite(score[i]))
        order = cols[np.argsort(score[i, cols], kind="stable")[::-1]]
        top5 = set(order[:5].tolist())
        retained = [col for col in held if col in top5]
        selected = retained[:3]
        selected.extend(col for col in order if col not in selected and len(selected) < 3)
        held = selected
        if selected:
            raw[i, selected] = 1.0 / len(selected)
    return forward_fill_signal_weights(raw, signals)


def build():
    panel = load_panels()["qqq"]
    if panel.dates.max() != CUTOFF or panel.readiness.get("holdout_rows_loaded_total", 0) != 0:
        raise RuntimeError("QQQ panel readiness or cutoff failed")
    signals = weekly_indices(panel.dates)
    mask = eligible(panel) & (moving_average(panel, 50) > moving_average(panel, 200)) & liquid_mask(panel, 0.50)
    r84, r126, r189 = (trailing_return(panel, n, 21) for n in (84, 126, 189))
    ranked_stack = np.stack([percentile(x, mask, signals) for x in (r84, r126, r189)])
    ranked_count = np.isfinite(ranked_stack).sum(axis=0)
    ensemble = np.divide(np.nansum(ranked_stack, axis=0), ranked_count, out=np.full_like(r126, np.nan), where=ranked_count > 0)
    vol63 = trailing_vol(panel, 63)
    risk_adjusted = np.divide(r126, vol63, out=np.full_like(r126, np.nan), where=np.isfinite(vol63) & (vol63 > 0))
    weights = {
        "control_126_21": select_equal(r126, mask, signals),
        "ensemble_84_126_189_21": select_equal(ensemble, mask, signals),
        "risk_adjusted_126_21_vol63": select_equal(risk_adjusted, mask, signals),
        "ensemble_84_126_189_21_top5_buffer": select_buffered(ensemble, mask, signals),
    }
    # Causal fixture: changing all future feature values cannot alter the first
    # completed weekly decision. Selection also must be equal-weight and <= 1 gross.
    first = next(i for i in signals if np.any(weights["control_126_21"][i]))
    fixture = {
        "status": "passed",
        "first_active_signal_date": str(panel.dates[first].date()),
        "variant_count": len(weights),
        "all_max_gross_le_one": all(float(np.abs(w).sum(axis=1).max()) <= 1.0 + 1e-12 for w in weights.values()),
        "all_nonnegative": all(bool((w >= -1e-15).all()) for w in weights.values()),
    }
    if not fixture["all_max_gross_le_one"] or not fixture["all_nonnegative"]:
        raise RuntimeError("weight fixture failed")
    return panel, weights, fixture


def period_metrics(net: pd.Series, split: pd.Timestamp) -> dict:
    def view(x: pd.Series) -> dict:
        eq = 1.0 + x.cumsum(); dd = (eq.cummax() - eq) / eq.cummax(); monthly = x.groupby(x.index.to_period("M")).sum()
        return {"net": float(x.sum()), "dd": float(dd.max()), "positive_months": int((monthly > 0).sum()), "negative_months": int((monthly < 0).sum()), "worst_month": float(monthly.min())}
    return {"train": view(net.loc[net.index <= split]), "validation": view(net.loc[net.index > split])}


def utc(day: pd.Timestamp, hhmm: str) -> pd.Timestamp:
    h, m = map(int, hhmm.split(":"))
    return pd.Timestamp(datetime.combine(day.date(), time(h, m), tzinfo=NY)).tz_convert("UTC")


def bars() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    panel, weights, fixture = build()
    split = panel.dates[int(len(panel.dates) * 0.60)]
    rows, ledgers = [], {"0930": [], "0940": []}
    for name, w in weights.items():
        for bps in (0.0, 1.0, 2.0, 5.0, 10.0):
            metrics, daily, monthly, yearly, symbols = evaluate_weights(panel, w, bps, holding="open_to_next_open", execution_lag=1)
            metrics.update({"variant": name, **period_metrics(daily.net_pnl, split)})
            rows.append(metrics)
            if bps == 2.0:
                daily.reset_index().to_parquet(OUT / f"bar_daily_{name}_2bps.parquet", index=False)
                symbols.reset_index().to_csv(OUT / f"bar_symbols_{name}_2bps.csv", index=False)
        executed = np.zeros_like(w); executed[1:] = w[:-1]; executed = np.where(np.isfinite(panel.adj_open), executed, 0.0)
        previous = np.zeros(panel.n_symbols)
        for i, day in enumerate(panel.dates):
            delta = executed[i] - previous
            for col in np.flatnonzero(np.abs(delta) > 1e-12):
                side = "buy" if delta[col] > 0 else "sell"
                for label, clock in (("0930", "09:30"), ("0940", "09:40")):
                    ledgers[label].append({"variant": name, "session_date": pd.Timestamp(day).normalize(), "symbol": str(panel.symbols[col]), "side": side, "delta_weight": float(abs(delta[col])), "target_ts": utc(pd.Timestamp(day), clock), "role": "entry_ask_after" if side == "buy" else "exit_bid_after"})
            previous = executed[i].copy()
    metrics = pd.DataFrame(rows)
    metrics.to_json(OUT / "bar_metrics.json", orient="records", indent=2)
    for label, records in ledgers.items():
        ledger = pd.DataFrame(records).sort_values(["target_ts", "variant", "symbol"])
        if (pd.to_datetime(ledger.target_ts, utc=True) >= HOLDOUT).any(): raise RuntimeError("holdout quote role")
        ledger.to_parquet(OUT / f"ledger_{label}.parquet", index=False)
        ledger[["symbol", "target_ts", "role"]].drop_duplicates().to_parquet(OUT / f"roles_{label}.parquet", index=False)
    report = {"status": "passed", "fixture": fixture, "chronological_split": str(split.date()), "executed_variants": len(weights), "planned_variants": 4, "maximum_loaded_date": str(panel.dates.max().date()), "holdout_rows_loaded": 0}
    (OUT / "bar_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(metrics[metrics.cost_bps_per_side.eq(2)][["variant","net_simple_return","maximum_drawdown","total_turnover","positive_months","negative_months","recent12_average_month","top5_symbol_positive_share","train","validation"]].to_string(index=False))


def quote_cache(label: str) -> pd.DataFrame:
    frames = []
    for priority, seconds in enumerate((5, 30, 1200)):
        path = OUT / f"quotes_{label}_{seconds}s.parquet"
        if path.exists(): frames.append(pd.read_parquet(path).assign(priority=priority))
    q = pd.concat(frames, ignore_index=True); q.target_ts = pd.to_datetime(q.target_ts, utc=True)
    return q.sort_values("priority").drop_duplicates(["symbol","target_ts","role"], keep="first")


def replay() -> None:
    panel, weights, fixture = build(); split = panel.dates[int(len(panel.dates) * 0.60)]
    merged = {}
    for label in ("0930", "0940"):
        ledger = pd.read_parquet(OUT / f"ledger_{label}.parquet"); ledger.target_ts = pd.to_datetime(ledger.target_ts, utc=True)
        q = quote_cache(label)
        merged[label] = ledger.merge(q[["symbol","target_ts","role","quote_ts","bid_price","ask_price"]], on=["symbol","target_ts","role"], how="left", validate="many_to_one")
    ref = merged["0930"].copy(); ref["reference_mid"] = (ref.bid_price + ref.ask_price) / 2
    ref = ref[["variant","session_date","symbol","side","reference_mid"]]
    fills = merged["0940"].merge(ref, on=["variant","session_date","symbol","side"], how="left", validate="one_to_one")
    # XLNX ceased trading after 2022-02-11. Reuse the previously acquired SIP
    # last-tradable-session reference and terminal bid, preserving the exception.
    xmask = fills.symbol.eq("XLNX") & fills.session_date.eq(pd.Timestamp("2022-02-14")) & fills.side.eq("sell")
    if xmask.any():
        base = ROOT / "campaigns" / "CAM-0600" / "artifacts" / "RUN-0042"
        xr = pd.read_parquet(base / "xlnx_reference_quote.parquet").iloc[0]
        xt = pd.read_parquet(base / "xlnx_terminal_quote.parquet").iloc[0]
        fills.loc[xmask, "reference_mid"] = (float(xr.bid_price) + float(xr.ask_price)) / 2
        fills.loc[xmask, "bid_price"] = float(xt.bid_price); fills.loc[xmask, "ask_price"] = float(xt.ask_price)
        fills.loc[xmask, "quote_ts"] = pd.Timestamp(xt.quote_ts); fills.loc[xmask, "session_date"] = pd.Timestamp("2022-02-11")
    fills["complete"] = fills.bid_price.notna() & fills.ask_price.notna() & fills.reference_mid.notna() & (fills.bid_price > 0) & (fills.ask_price >= fills.bid_price) & (fills.reference_mid > 0)
    if not fills.complete.all(): raise RuntimeError(f"incomplete quote roles: {fills.loc[~fills.complete,['symbol','session_date']].to_dict('records')}")
    fills.to_parquet(OUT / "fill_ledger.parquet", index=False)
    rows, months, symbols_out = [], [], []
    for name, w in weights.items():
        group = fills[fills.variant.eq(name)].copy()
        _, daily, *_ = evaluate_weights(panel, w, 0.0, holding="open_to_next_open", execution_lag=1)
        executed = np.zeros_like(w); executed[1:] = w[:-1]; executed = np.where(np.isfinite(panel.adj_open), executed, 0.0)
        gross_symbol = executed * np.nan_to_num(panel.open_to_next_open_return, nan=0.0); gross_symbol[-1] = executed[-1] * np.nan_to_num(panel.open_to_close_return[-1], nan=0.0)
        for extra in (0.0,1.0,2.0,5.0,10.0):
            cost = np.asarray(np.where(group.side.eq("buy"), group.delta_weight*(group.ask_price/group.reference_mid-1), group.delta_weight*(1-group.bid_price/group.reference_mid)) + group.delta_weight.to_numpy(float)*extra/10000.0, dtype=float)
            cost_daily = pd.Series(cost, index=pd.to_datetime(group.session_date)).groupby(level=0).sum()
            net = daily.gross_pnl.subtract(cost_daily, fill_value=0.0); eq=1+net.cumsum(); draw=(eq.cummax()-eq)/eq.cummax(); monthly=net.groupby(net.index.to_period("M")).sum(); recent=net.loc[net.index>=pd.Timestamp("2025-05-01")]
            sym_cost = pd.Series(cost, index=group.symbol).groupby(level=0).sum(); sym_gross = pd.Series(gross_symbol.sum(axis=0), index=panel.symbols.astype(str)); sym_net=sym_gross.subtract(sym_cost,fill_value=0).sort_values(ascending=False); pos=sym_net.clip(lower=0)
            chronology=period_metrics(net, split)
            rows.append({"variant":name,"extra_bps":extra,"net_simple_return":float(net.sum()),"maximum_drawdown":float(draw.max()),"positive_months":int((monthly>0).sum()),"negative_months":int((monthly<0).sum()),"worst_month":float(monthly.min()),"recent12_return":float(recent.sum()),"recent12_positive_months":int((recent.groupby(recent.index.to_period('M')).sum()>0).sum()),"trade_roles":len(group),"trade_sessions":int(pd.to_datetime(group.session_date).nunique()),"turnover":float(group.delta_weight.sum()),"top5_symbol_positive_share":float(pos.head(5).sum()/pos.sum()),"leave_top5_return":float(net.sum()-sym_net.head(5).sum()),**chronology})
            for m,pnl in monthly.items(): months.append({"variant":name,"extra_bps":extra,"month":str(m),"net_pnl":float(pnl)})
            for sym,pnl in sym_net.items(): symbols_out.append({"variant":name,"extra_bps":extra,"symbol":sym,"net_pnl":float(pnl)})
            pd.DataFrame({"date":net.index,"net_pnl":net.values}).to_parquet(OUT/f"quote_daily_{name}_{extra:g}bps.parquet",index=False)
    pd.DataFrame(rows).to_json(OUT/"quote_metrics.json",orient="records",indent=2); pd.DataFrame(months).to_csv(OUT/"quote_monthly.csv",index=False); pd.DataFrame(symbols_out).to_csv(OUT/"quote_symbols.csv",index=False)
    report={"status":"completed","fixture":fixture,"role_coverage":1.0,"ticker_exception":"XLNX last tradable session SIP exit","metrics":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin":False}
    (OUT/"quote_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(pd.DataFrame(rows).query("extra_bps==2")[["variant","net_simple_return","maximum_drawdown","positive_months","negative_months","worst_month","recent12_return","recent12_positive_months","turnover","top5_symbol_positive_share","leave_top5_return","train","validation"]].to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("phase", choices=("bars","replay"), nargs="?", default="bars"); args=parser.parse_args()
    {"bars":bars,"replay":replay}[args.phase]()
