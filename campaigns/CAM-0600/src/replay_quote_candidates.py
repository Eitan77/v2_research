from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


WORKSPACE = Path(__file__).resolve().parents[3]
CAMPAIGNS = WORKSPACE / "campaigns"
SHARED = CAMPAIGNS / "CAM-0600" / "artifacts" / "shared"
SLIPPAGE = (0.0, 1.0, 2.0, 5.0)


def maximum_drawdown(net: pd.Series) -> float:
    equity = 1.0 + net.cumsum()
    peak = equity.cummax()
    return float(((peak-equity)/peak).max()) if len(equity) else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delayed", action="store_true")
    parser.add_argument("--delayed-final", action="store_true")
    args = parser.parse_args()
    if args.delayed or args.delayed_final:
        ledger_path = SHARED / "quote_candidate_positions_0940.parquet"
        match_specs = (
            (SHARED / "remote_quote_role_matches_0940.parquet", 1),
            (SHARED / "remote_quote_role_matches_0940_expanded.parquet", 5),
            (SHARED / "local_quote_role_matches_0940.parquet", 5),
            (SHARED / "remote_quote_role_matches_0940_final.parquet", 30),
        )
        if args.delayed_final:
            match_specs = match_specs + ((SHARED / "remote_quote_role_matches_0940_last.parquet", 120),)
            run_id, suffix = "RUN-0007", "_0940_final"
        else:
            run_id, suffix = "RUN-0006", "_0940"
        model = "09:40 marketable ask entry/open exit and 16:00 bid intraday exit; conservative daily reset"
    else:
        ledger_path = SHARED / "quote_candidate_positions.parquet"
        match_specs = (
            (SHARED / "remote_quote_role_matches.parquet", 1),
            (SHARED / "remote_quote_role_matches_expanded.parquet", 5),
            (SHARED / "remote_quote_role_matches_final.parquet", 30),
        )
        run_id, suffix, model = "RUN-0005", "", "marketable ask entry and bid exit; conservative daily reset"
    ledger = pd.read_parquet(ledger_path)
    matches = []
    for path, window_seconds in match_specs:
        if path.exists():
            frame = pd.read_parquet(path)
            if "match_valid" in frame.columns:
                frame = frame[frame["match_valid"].fillna(False).astype(bool)]
            frame["window_seconds"] = window_seconds
            matches.append(frame)
    quotes = pd.concat(matches, ignore_index=True)
    quotes = quotes.sort_values("window_seconds").drop_duplicates(["symbol", "target_ts", "role"], keep="first")
    entry = quotes[quotes["role"] == "entry_ask_after"].copy()
    entry = entry.rename(columns={
        "target_ts": "entry_target_ts", "quote_ts": "entry_quote_ts", "ask_price": "entry_ask",
        "bid_price": "entry_bid", "ask_size": "entry_ask_size", "bid_size": "entry_bid_size",
        "window_seconds": "entry_window_seconds",
    })
    exit_quotes = quotes[quotes["role"].isin(["exit_bid_after", "exit_bid_before"])].copy()
    exit_quotes = exit_quotes.rename(columns={
        "target_ts": "exit_target_ts", "quote_ts": "exit_quote_ts", "bid_price": "exit_bid",
        "ask_price": "exit_ask", "bid_size": "exit_bid_size", "ask_size": "exit_ask_size",
        "window_seconds": "exit_window_seconds",
    })
    replay = ledger.merge(
        entry[["symbol", "entry_target_ts", "entry_quote_ts", "entry_bid", "entry_ask", "entry_bid_size", "entry_ask_size", "entry_window_seconds"]],
        on=["symbol", "entry_target_ts"], how="left", validate="many_to_one",
    ).merge(
        exit_quotes[["symbol", "exit_target_ts", "exit_quote_ts", "exit_bid", "exit_ask", "exit_bid_size", "exit_ask_size", "exit_window_seconds"]],
        on=["symbol", "exit_target_ts"], how="left", validate="many_to_one",
    )
    replay["quote_complete"] = (
        replay["entry_ask"].notna() & replay["exit_bid"].notna()
        & (replay["entry_ask"] > 0) & (replay["exit_bid"] > 0)
    )
    replay["entry_spread_bps"] = (replay["entry_ask"]-replay["entry_bid"]) / ((replay["entry_ask"]+replay["entry_bid"])/2) * 10000
    replay["exit_spread_bps"] = (replay["exit_ask"]-replay["exit_bid"]) / ((replay["exit_ask"]+replay["exit_bid"])/2) * 10000
    replay["entry_delay_ms"] = (pd.to_datetime(replay["entry_quote_ts"], utc=True)-pd.to_datetime(replay["entry_target_ts"], utc=True)).dt.total_seconds()*1000
    before = replay["holding"].eq("open_to_close")
    exit_delta = (pd.to_datetime(replay["exit_quote_ts"], utc=True)-pd.to_datetime(replay["exit_target_ts"], utc=True)).dt.total_seconds()*1000
    replay["exit_role_delay_ms"] = np.where(before, -exit_delta, exit_delta)
    replay["marketable_return"] = replay["exit_bid"]/replay["entry_ask"]-1.0
    replay.to_parquet(SHARED / f"quote_position_replay{suffix}.parquet", index=False)

    all_metrics = []
    summary_rows = []
    for campaign_id, group in replay.groupby("campaign_id", sort=True):
        complete = group[group["quote_complete"]].copy()
        output = CAMPAIGNS / campaign_id / "artifacts" / run_id
        output.mkdir(parents=True, exist_ok=True)
        group.to_parquet(output / "position_replay.parquet", index=False)
        campaign_metrics = []
        for slippage in SLIPPAGE:
            pnl = complete["target_weight"] * (
                complete["marketable_return"] - 2.0*slippage/10000.0
            )
            daily = pnl.groupby(complete["session_date"]).sum().sort_index()
            monthly = daily.groupby(pd.DatetimeIndex(daily.index).to_period("M")).sum()
            symbol_pnl = pnl.groupby(complete["symbol"]).sum().sort_values(ascending=False)
            positive = symbol_pnl.clip(lower=0)
            record = {
                "campaign_id": campaign_id,
                "variant_id": str(group["variant_id"].iloc[0]),
                "extra_slippage_bps_per_side": slippage,
                "net_simple_return": float(daily.sum()),
                "maximum_drawdown": maximum_drawdown(daily),
                "position_rows": int(len(group)),
                "quote_complete_position_rows": int(len(complete)),
                "missing_position_rows": int((~group["quote_complete"]).sum()),
                "position_coverage_rate": float(group["quote_complete"].mean()),
                "symbols": int(group["symbol"].nunique()),
                "active_sessions": int(len(daily)),
                "green_sessions": int((daily > 0).sum()),
                "red_sessions": int((daily < 0).sum()),
                "positive_months": int((monthly > 0).sum()),
                "negative_months": int((monthly < 0).sum()),
                "monthly_average": float(monthly.mean()),
                "monthly_median": float(monthly.median()),
                "recent12_average_month": float(monthly.iloc[-12:].mean()),
                "top5_symbol_positive_share": float(positive.head(5).sum()/positive.sum()) if positive.sum() > 0 else None,
                "leave_best_symbol_out_return": float(daily.sum()-symbol_pnl.iloc[0]) if len(symbol_pnl) else None,
                "mean_entry_spread_bps": float(complete["entry_spread_bps"].mean()),
                "mean_exit_spread_bps": float(complete["exit_spread_bps"].mean()),
                "median_entry_delay_ms": float(complete["entry_delay_ms"].median()),
                "median_exit_role_delay_ms": float(complete["exit_role_delay_ms"].median()),
                "max_role_window_seconds": int(max(complete["entry_window_seconds"].max(), complete["exit_window_seconds"].max())),
                "broker_margin": False,
                "direct_short": False,
                "holdout_rows_loaded": 0,
            }
            campaign_metrics.append(record)
            all_metrics.append(record)
            if slippage == 0:
                daily.rename("net_pnl").rename_axis("date").reset_index().to_parquet(output / "daily_0bps_extra.parquet", index=False)
        metrics_frame = pd.DataFrame(campaign_metrics)
        metrics_frame.to_csv(output / "quote_metrics.csv", index=False)
        central = metrics_frame[metrics_frame["extra_slippage_bps_per_side"] == 0].iloc[0]
        decision = (
            "quote_survives_complete" if central["net_simple_return"] > 0 and central["position_coverage_rate"] == 1.0
            else "quote_survives_incomplete" if central["net_simple_return"] > 0
            else "quote_rejected"
        )
        report = {
            "status": "completed", "campaign_id": campaign_id, "run_id": run_id,
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "quote_model": model,
            "window_start": "2025-05-01", "window_end": "2026-04-30",
            "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0,
            "broker_margin": False, "direct_short": False, "decision": decision,
            "promotion_ready": False,
            "incompleteness_blocks_full_execution_claim": bool(central["position_coverage_rate"] < 1.0),
            "metrics": campaign_metrics,
        }
        (output / "execution_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        summary_rows.append({"campaign_id": campaign_id, "variant_id": central["variant_id"],
                             "decision": decision, "quote_net_return": central["net_simple_return"],
                             "maximum_drawdown": central["maximum_drawdown"],
                             "coverage_rate": central["position_coverage_rate"],
                             "positive_months": central["positive_months"], "negative_months": central["negative_months"],
                             "mean_entry_spread_bps": central["mean_entry_spread_bps"],
                             "mean_exit_spread_bps": central["mean_exit_spread_bps"]})
    pd.DataFrame(all_metrics).to_csv(SHARED / f"all_quote_metrics{suffix}.csv", index=False)
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(SHARED / f"quote_summary{suffix}.csv", index=False)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
