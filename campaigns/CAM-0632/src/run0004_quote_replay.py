from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "reference_code"))
from ar_pipeline.marketdata.alpaca import AlpacaHistoricalClient, CachedResponseStore, QuoteRequest, fetch_quote_requests

from run0002_structural_scalps import build_contexts, load_bars, max_drawdown_and_recovery


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0004"
SOURCE = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0003" / "top_trade_ledgers.csv"
CACHE = ROOT / "tmp" / "cam0632_run0004_quotes"
VARIANTS = [
    "SMH_SOXL_SOXS_reversal_t60_h15_none_ov0",
    "SMH_SOXL_SOXS_reversal_t50_h20_none_ov20",
    "QQQ_TQQQ_SQQQ_reversal_t40_h15_one_bar_opposite_ov0",
]
LATENCIES = [0, 250, 1000]
EXTRA_BP = [0, 1, 2, 5]


def metrics(frame: pd.DataFrame, calendar: pd.DatetimeIndex, extra_bp: int) -> dict:
    frame = frame.copy()
    frame["net_return"] = frame.exit_bid * (1 - extra_bp / 10000) / (frame.entry_ask * (1 + extra_bp / 10000)) - 1
    daily = pd.Series(0.0, index=calendar)
    realized = frame.groupby("date").net_return.sum()
    daily.loc[pd.to_datetime(realized.index)] = realized.to_numpy()
    monthly = daily.groupby(daily.index.to_period("M")).sum()
    recent12 = pd.Timestamp("2025-05-01")
    recent18 = pd.Timestamp("2024-11-01")
    dd, recovery = max_drawdown_and_recovery(daily)
    blocks = np.array_split(daily, 3)
    active = daily[daily != 0]
    return {
        "trades": len(frame), "net_return": float(daily.sum()),
        "recent12_return": float(daily[daily.index >= recent12].sum()),
        "recent18_return": float(daily[daily.index >= recent18].sum()),
        "mean_trade_bp": float(frame.net_return.mean() * 10000),
        "max_drawdown": dd, "recovery_sessions": recovery,
        "positive_month_fraction": float((monthly > 0).mean()),
        "green_all_days": float((daily > 0).mean()),
        "green_active_days": float((active > 0).mean()) if len(active) else np.nan,
        "block_returns": [float(block.sum()) for block in blocks],
        "worst_day": float(daily.min()),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    trades = pd.read_csv(SOURCE, parse_dates=["date", "signal_ts", "entry_ts", "exit_ts"])
    trades = trades[trades.variant.isin(VARIANTS)].drop_duplicates(["variant", "date", "symbol", "entry_ts", "exit_ts"])
    if set(trades.variant) != set(VARIANTS):
        raise RuntimeError("selected variant missing from frozen source ledger")
    roles = []
    for trade_id, row in enumerate(trades.itertuples()):
        roles.append({"trade_id": trade_id, "variant": row.variant, "date": row.date, "symbol": row.symbol, "role": "entry", "target_ts": pd.Timestamp(row.entry_ts)})
        roles.append({"trade_id": trade_id, "variant": row.variant, "date": row.date, "symbol": row.symbol, "role": "exit", "target_ts": pd.Timestamp(row.exit_ts)})
    roles = pd.DataFrame(roles)
    roles.target_ts = pd.to_datetime(roles.target_ts, utc=True)
    unique = roles[["symbol", "target_ts"]].drop_duplicates()
    requests = [QuoteRequest(row.symbol, row.target_ts, row.target_ts + pd.Timedelta(seconds=30), "sip") for row in unique.itertuples()]
    client = AlpacaHistoricalClient.from_env(ROOT / ".env.local")
    client.requests_per_minute = 180
    frames = fetch_quote_requests(requests, client=client, cache=CachedResponseStore(CACHE), workers=12)
    lookup = {(request.symbol, request.start): frame for request, frame in frames.items()}
    quote_rows = []
    for row in roles.itertuples():
        frame = lookup[(row.symbol, row.target_ts)]
        for latency in LATENCIES:
            arrival = row.target_ts + pd.Timedelta(milliseconds=latency)
            eligible = frame[(frame.timestamp >= arrival) & (frame.timestamp <= row.target_ts + pd.Timedelta(seconds=30))]
            quote = eligible.iloc[0] if len(eligible) else None
            quote_rows.append({"trade_id": row.trade_id, "variant": row.variant, "date": row.date, "symbol": row.symbol, "role": row.role, "target_ts": row.target_ts, "latency_ms": latency, "quote_ts": quote.timestamp if quote is not None else pd.NaT, "bid": quote.bid_price if quote is not None else np.nan, "ask": quote.ask_price if quote is not None else np.nan, "bid_size_lots": quote.bid_size if quote is not None else np.nan, "ask_size_lots": quote.ask_size if quote is not None else np.nan})
    quotes = pd.DataFrame(quote_rows)
    coverage = quotes.groupby(["variant", "latency_ms"]).quote_ts.apply(lambda values: values.notna().mean()).reset_index(name="role_coverage")
    if (coverage.role_coverage < 1).any():
        coverage.to_csv(OUT / "coverage.csv", index=False)
        quotes[quotes.quote_ts.isna()].to_csv(OUT / "missing_roles.csv", index=False)
        raise RuntimeError("quote endpoint coverage below 100%")
    wide = quotes.pivot(index=["trade_id", "variant", "date", "symbol", "latency_ms"], columns="role", values=["bid", "ask", "bid_size_lots", "ask_size_lots", "quote_ts"]).reset_index()
    wide.columns = ["_".join([str(value) for value in column if str(value)]) if isinstance(column, tuple) else column for column in wide.columns]
    wide = wide.rename(columns={"ask_entry": "entry_ask", "bid_exit": "exit_bid", "ask_size_lots_entry": "entry_ask_size_lots", "bid_size_lots_exit": "exit_bid_size_lots", "quote_ts_entry": "entry_quote_ts", "quote_ts_exit": "exit_quote_ts"})
    wide["entry_ask"] = pd.to_numeric(wide.entry_ask, errors="raise")
    wide["exit_bid"] = pd.to_numeric(wide.exit_bid, errors="raise")
    calendars = {}
    contexts, _, _ = build_contexts(load_bars())
    for variant in VARIANTS:
        pair = "SMH_SOXL_SOXS" if variant.startswith("SMH") else "QQQ_TQQQ_SQQQ"
        calendars[variant] = pd.DatetimeIndex(sorted(context["date"] for context in contexts[pair]))
    rows = []
    period_rows = []
    ledgers = []
    for (variant, latency), group in wide.groupby(["variant", "latency_ms"]):
        group = group.copy()
        group.date = pd.to_datetime(group.date)
        for extra in EXTRA_BP:
            result = metrics(group, calendars[variant], extra)
            rows.append({"variant": variant, "latency_ms": latency, "extra_bp_side": extra, "role_coverage": 1.0, **result})
            g = group.copy()
            g["extra_bp_side"] = extra
            g["net_return"] = g.exit_bid * (1 - extra / 10000) / (g.entry_ask * (1 + extra / 10000)) - 1
            ledgers.append(g)
            daily = g.groupby("date").net_return.sum().reindex(calendars[variant], fill_value=0.0)
            for period_type, series in [("weekly", daily.groupby(daily.index.to_period("W-FRI")).sum()), ("monthly", daily.groupby(daily.index.to_period("M")).sum()), ("yearly", daily.groupby(daily.index.to_period("Y")).sum())]:
                period_rows.extend({"variant": variant, "latency_ms": latency, "extra_bp_side": extra, "period_type": period_type, "period": str(period), "net_return": float(value)} for period, value in series.items())
    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "quote_summary.csv", index=False)
    coverage.to_csv(OUT / "coverage.csv", index=False)
    pd.DataFrame(period_rows).to_csv(OUT / "period_paths.csv", index=False)
    pd.concat(ledgers, ignore_index=True).to_csv(OUT / "quote_ledgers.csv", index=False)
    primary = summary[(summary.latency_ms == 250) & (summary.extra_bp_side == 2)]
    report = {
        "status": "completed", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "selected_variants": VARIANTS, "source_trades": len(trades), "quote_roles": len(roles),
        "unique_quote_requests": len(requests), "minimum_role_coverage": float(coverage.role_coverage.min()),
        "planned_execution_rows": len(VARIANTS) * len(LATENCIES) * len(EXTRA_BP), "executed_execution_rows": len(summary),
        "primary_250ms_2bp": json.loads(primary.to_json(orient="records")),
        "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0,
        "decision_gate": "execution_survival_does_not_override_sparse_inconsistent_profile",
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
