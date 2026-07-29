from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from cam0005 import CUTOFF, max_drawdown_and_recovery
from run0006 import first_trade, select_quote


WINDOW_STARTS = {
    "18m": pd.Timestamp("2024-11-01"),
    "15m": pd.Timestamp("2025-02-01"),
    "12m": pd.Timestamp("2025-05-01"),
}


def build_replay(
    events: pd.DataFrame,
    entry_quotes: pd.DataFrame,
    entry_trades: pd.DataFrame,
    exit_quotes: pd.DataFrame,
    exit_trades: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict] = []
    for item in events.itertuples():
        entry = select_quote(entry_quotes, item.event_id, "entry")
        exit_ = select_quote(exit_quotes, item.event_id, "exit_0935")
        entry_trade = first_trade(entry_trades, item.event_id, "entry")
        exit_trade = first_trade(exit_trades, item.event_id, "exit_0935")
        if entry is None or exit_ is None:
            rows.append({**item._asdict(), "quote_complete": False})
            continue
        entry_ask = float(entry["ap"])
        exit_bid = float(exit_["bp"])
        rows.append(
            {
                **item._asdict(),
                "quote_complete": True,
                "trade_complete": entry_trade is not None and exit_trade is not None,
                "entry_ask": entry_ask,
                "entry_bid": float(entry["bp"]),
                "exit_bid": exit_bid,
                "exit_ask": float(exit_["ap"]),
                "entry_ask_size_raw": float(entry["as"]),
                "exit_bid_size_raw": float(exit_["bs"]),
                "entry_delay_ms": (
                    pd.Timestamp(entry["t"]) - pd.Timestamp(entry["target_ts"])
                ).total_seconds()
                * 1000,
                "exit_delay_ms": (
                    pd.Timestamp(exit_["t"]) - pd.Timestamp(exit_["target_ts"])
                ).total_seconds()
                * 1000,
                "entry_spread_bps": (
                    (float(entry["ap"]) - float(entry["bp"]))
                    / ((float(entry["ap"]) + float(entry["bp"])) / 2)
                    * 10_000
                ),
                "exit_spread_bps": (
                    (float(exit_["ap"]) - float(exit_["bp"]))
                    / ((float(exit_["ap"]) + float(exit_["bp"])) / 2)
                    * 10_000
                ),
                "nbbo_gross_return": exit_bid / entry_ask - 1,
            }
        )
    return pd.DataFrame(rows)


def summarize(replay: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict] = []
    month_rows: list[dict] = []
    months = pd.period_range("2024-11", "2026-04", freq="M")
    for threshold in ("q50", "q60"):
        base = replay[replay[f"is_{threshold}"] & replay["quote_complete"]].copy()
        for slippage in (2, 5, 10):
            frame = base.copy()
            frame["net_pnl"] = frame["nbbo_gross_return"] - 2 * slippage / 10_000
            daily = frame.groupby("date", as_index=False)["net_pnl"].sum()
            monthly = (
                daily.assign(month=pd.to_datetime(daily["date"]).dt.to_period("M"))
                .groupby("month")["net_pnl"]
                .sum()
                .reindex(months, fill_value=0.0)
            )
            dd, recovery, unresolved = max_drawdown_and_recovery(daily)
            total = float(daily["net_pnl"].sum())
            variant = f"{threshold}_0935_nbbo_slip{slippage}"
            row = {
                "variant": variant,
                "threshold": threshold,
                "additional_slippage_bps_per_side": slippage,
                "full_net_simple_return": total,
                "standard_max_drawdown": dd,
                "max_recovery_days": recovery,
                "recovery_unresolved": unresolved,
                "trade_count": int(len(frame)),
                "quote_coverage_fraction": float(
                    len(frame) / replay[f"is_{threshold}"].sum()
                ),
                "trade_evidence_fraction": float(frame["trade_complete"].mean()),
                "median_entry_spread_bps": float(frame["entry_spread_bps"].median()),
                "median_exit_spread_bps": float(frame["exit_spread_bps"].median()),
                "median_entry_delay_ms": float(frame["entry_delay_ms"].median()),
                "median_exit_delay_ms": float(frame["exit_delay_ms"].median()),
                "top_5_day_profit_share": (
                    float(daily["net_pnl"].nlargest(5).sum() / total)
                    if total > 0 else float("nan")
                ),
            }
            for label, start in WINDOW_STARTS.items():
                subset = monthly[monthly.index >= start.to_period("M")]
                row[f"average_month_{label}"] = float(subset.mean())
                row[f"negative_months_{label}"] = int((subset < 0).sum())
            rows.append(row)
            for month, pnl in monthly.items():
                month_rows.append(
                    {"variant": variant, "month": str(month), "net_pnl": float(pnl)}
                )
    return pd.DataFrame(rows), pd.DataFrame(month_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-path", type=Path, required=True)
    parser.add_argument("--entry-quotes-path", type=Path, required=True)
    parser.add_argument("--entry-trades-path", type=Path, required=True)
    parser.add_argument("--exit-quotes-path", type=Path, required=True)
    parser.add_argument("--exit-trades-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    events = pd.read_parquet(args.events_path)
    events["next_session"] = pd.to_datetime(events["next_session"])
    if events["next_session"].max() > CUTOFF:
        raise RuntimeError("Sealed holdout row loaded")
    replay = build_replay(
        events,
        pd.read_parquet(args.entry_quotes_path),
        pd.read_parquet(args.entry_trades_path),
        pd.read_parquet(args.exit_quotes_path),
        pd.read_parquet(args.exit_trades_path),
    )
    variants, monthly = summarize(replay)
    if len(variants) != 6:
        raise RuntimeError(f"Expected 6 variants, got {len(variants)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    replay.to_parquet(args.output_dir / "event_replay.parquet", index=False)
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    diagnostics = {
        "status": "passed",
        "event_count": int(len(replay)),
        "quote_complete_events": int(replay["quote_complete"].sum()),
        "trade_complete_events": int(replay["trade_complete"].sum()),
        "max_exit_session": str(events["next_session"].max().date()),
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2), encoding="utf-8"
    )
    contract = {
        "command": (
            "python campaigns/CAM-0005/src/run0008.py "
            "--events-path campaigns/CAM-0005/artifacts/RUN-0006/raw/events.parquet "
            "--entry-quotes-path campaigns/CAM-0005/artifacts/RUN-0006/raw/quotes.parquet "
            "--entry-trades-path campaigns/CAM-0005/artifacts/RUN-0006/raw/trades.parquet "
            "--exit-quotes-path campaigns/CAM-0005/artifacts/RUN-0008/raw/quotes.parquet "
            "--exit-trades-path campaigns/CAM-0005/artifacts/RUN-0008/raw/trades.parquet "
            "--output-dir campaigns/CAM-0005/artifacts/RUN-0008"
        ),
        "resolved_defaults": {
            "thresholds": ["q50", "q60"],
            "exit": "first_noncrossed_bid_at_or_after_09:35",
            "additional_slippage_bps_per_side": [2, 5, 10],
        },
        "executed_variant_count": int(len(variants)),
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2), encoding="utf-8"
    )
    print(variants.to_string(index=False))
    print(json.dumps(diagnostics, indent=2))


if __name__ == "__main__":
    main()
