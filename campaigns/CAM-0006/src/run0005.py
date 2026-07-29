from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0006 import CUTOFF, max_drawdown_and_recovery


WINDOW_STARTS = {
    "18m": pd.Timestamp("2024-11-01"),
    "15m": pd.Timestamp("2025-02-01"),
    "12m": pd.Timestamp("2025-05-01"),
}
BLOCKS = (
    ("block_1", pd.Timestamp("2024-11-01"), pd.Timestamp("2025-04-30")),
    ("block_2", pd.Timestamp("2025-05-01"), pd.Timestamp("2025-10-31")),
    ("block_3", pd.Timestamp("2025-11-01"), pd.Timestamp("2026-04-30")),
)


def select_quote(quotes: pd.DataFrame, event_id: str, phase: str) -> pd.Series | None:
    frame = quotes[
        quotes["event_id"].eq(event_id) & quotes["phase"].eq(phase)
    ].copy()
    if frame.empty:
        return None
    frame["t"] = pd.to_datetime(frame["t"], utc=True)
    frame["target_ts"] = pd.to_datetime(frame["target_ts"], utc=True)
    frame = frame[
        frame["t"].ge(frame["target_ts"])
        & frame["ap"].gt(0)
        & frame["bp"].gt(0)
        & frame["ap"].ge(frame["bp"])
    ].sort_values("t")
    return None if frame.empty else frame.iloc[0]


def first_trade(trades: pd.DataFrame, event_id: str, phase: str) -> pd.Series | None:
    frame = trades[
        trades["event_id"].eq(event_id) & trades["phase"].eq(phase)
    ].copy()
    if frame.empty:
        return None
    frame["t"] = pd.to_datetime(frame["t"], utc=True)
    frame["target_ts"] = pd.to_datetime(frame["target_ts"], utc=True)
    frame = frame[frame["t"].ge(frame["target_ts"])].sort_values("t")
    return None if frame.empty else frame.iloc[0]


def replay_events(
    events: pd.DataFrame, quotes: pd.DataFrame, trades: pd.DataFrame
) -> pd.DataFrame:
    rows = []
    for item in events.itertuples(index=False):
        entry = select_quote(quotes, item.event_id, "entry")
        exit_ = select_quote(quotes, item.event_id, "exit")
        entry_trade = first_trade(trades, item.event_id, "entry")
        exit_trade = first_trade(trades, item.event_id, "exit")
        base = item._asdict()
        if entry is None or exit_ is None:
            rows.append(
                {
                    **base,
                    "quote_complete": False,
                    "trade_complete": entry_trade is not None and exit_trade is not None,
                }
            )
            continue
        entry_ask = float(entry["ap"])
        entry_bid = float(entry["bp"])
        exit_bid = float(exit_["bp"])
        exit_ask = float(exit_["ap"])
        bar_gross = float(item.exit_final / item.entry_open - 1.0)
        nbbo_gross = float(exit_bid / entry_ask - 1.0)
        rows.append(
            {
                **base,
                "quote_complete": True,
                "trade_complete": entry_trade is not None and exit_trade is not None,
                "entry_ask": entry_ask,
                "entry_bid": entry_bid,
                "exit_bid": exit_bid,
                "exit_ask": exit_ask,
                "entry_ask_size_raw": float(entry.get("as", np.nan)),
                "exit_bid_size_raw": float(exit_.get("bs", np.nan)),
                "entry_delay_ms": float(
                    (pd.Timestamp(entry["t"]) - pd.Timestamp(entry["target_ts"]))
                    .total_seconds()
                    * 1000.0
                ),
                "exit_delay_ms": float(
                    (pd.Timestamp(exit_["t"]) - pd.Timestamp(exit_["target_ts"]))
                    .total_seconds()
                    * 1000.0
                ),
                "entry_spread_bps": (
                    (entry_ask - entry_bid) / ((entry_ask + entry_bid) / 2.0) * 10_000.0
                ),
                "exit_spread_bps": (
                    (exit_ask - exit_bid) / ((exit_ask + exit_bid) / 2.0) * 10_000.0
                ),
                "first_entry_trade": (
                    float(entry_trade["p"]) if entry_trade is not None else np.nan
                ),
                "first_exit_trade": (
                    float(exit_trade["p"]) if exit_trade is not None else np.nan
                ),
                "bar_gross_return": bar_gross,
                "nbbo_gross_return": nbbo_gross,
                "nbbo_minus_bar_bps": (nbbo_gross - bar_gross) * 10_000.0,
            }
        )
    return pd.DataFrame(rows)


def summarize(
    replay: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows = []
    month_rows = []
    block_rows = []
    months = pd.period_range("2024-11", "2026-04", freq="M")
    for candidate in ("all_state", "vol_high"):
        requested = replay[f"is_{candidate}"]
        complete = requested & replay["quote_complete"]
        base = replay[complete].copy()
        for slippage in (0, 2, 5):
            frame = base.copy()
            frame["net_pnl"] = (
                frame["nbbo_gross_return"] - 2.0 * slippage / 10_000.0
            )
            daily = frame[["date", "net_pnl"]].copy()
            monthly = (
                daily.assign(month=daily["date"].dt.to_period("M"))
                .groupby("month")["net_pnl"]
                .sum()
                .reindex(months, fill_value=0.0)
            )
            dd, recovery, unresolved = max_drawdown_and_recovery(daily)
            total = float(daily["net_pnl"].sum())
            symbol_pnl = frame.groupby("symbol")["net_pnl"].sum()
            variant = f"{candidate}_marketable_nbbo_slip{slippage}"
            row = {
                "variant": variant,
                "candidate": candidate,
                "additional_slippage_bps_per_side": slippage,
                "full_net_simple_return": total,
                "standard_max_drawdown": dd,
                "max_recovery_days": recovery,
                "recovery_unresolved": unresolved,
                "event_count": int(len(frame)),
                "symbol_count": int(frame["symbol"].nunique()),
                "quote_coverage_fraction": float(len(frame) / requested.sum()),
                "trade_evidence_fraction": float(frame["trade_complete"].mean()),
                "median_entry_spread_bps": float(frame["entry_spread_bps"].median()),
                "median_exit_spread_bps": float(frame["exit_spread_bps"].median()),
                "q90_entry_spread_bps": float(frame["entry_spread_bps"].quantile(0.90)),
                "q90_exit_spread_bps": float(frame["exit_spread_bps"].quantile(0.90)),
                "median_entry_delay_ms": float(frame["entry_delay_ms"].median()),
                "median_exit_delay_ms": float(frame["exit_delay_ms"].median()),
                "median_entry_ask_size_raw": float(frame["entry_ask_size_raw"].median()),
                "median_exit_bid_size_raw": float(frame["exit_bid_size_raw"].median()),
                "median_nbbo_minus_bar_bps": float(
                    frame["nbbo_minus_bar_bps"].median()
                ),
                "top_5_day_profit_share": (
                    float(daily["net_pnl"].nlargest(5).sum() / total)
                    if total > 0
                    else np.nan
                ),
                "top_symbol_profit_share": (
                    float(symbol_pnl.max() / total) if total > 0 else np.nan
                ),
            }
            for label, start in WINDOW_STARTS.items():
                subset = monthly[monthly.index >= start.to_period("M")]
                row[f"average_month_{label}"] = float(subset.mean())
                row[f"negative_months_{label}"] = int((subset < 0).sum())
                row[f"zero_months_{label}"] = int((subset == 0).sum())
            rows.append(row)
            for month, value in monthly.items():
                month_rows.append(
                    {"variant": variant, "month": str(month), "net_pnl": float(value)}
                )
            for block, start, end in BLOCKS:
                sub = daily[daily["date"].between(start, end)]
                block_rows.append(
                    {
                        "variant": variant,
                        "block": block,
                        "net_pnl": float(sub["net_pnl"].sum()),
                        "event_count": int(len(sub)),
                    }
                )
    return pd.DataFrame(rows), pd.DataFrame(month_rows), pd.DataFrame(block_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    events = pd.read_parquet(args.raw_dir / "events.parquet")
    quotes = pd.read_parquet(args.raw_dir / "quotes.parquet")
    trades = pd.read_parquet(args.raw_dir / "trades.parquet")
    events["date"] = pd.to_datetime(events["date"])
    if events["date"].max() > CUTOFF:
        raise RuntimeError("Sealed event loaded")
    replay = replay_events(events, quotes, trades)
    variants, monthly, blocks = summarize(replay)
    if len(variants) != 6:
        raise RuntimeError(f"Expected 6 variants, got {len(variants)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    replay.to_parquet(args.output_dir / "event_replay.parquet", index=False)
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    blocks.to_csv(args.output_dir / "blocks.csv", index=False)
    diagnostics = {
        "status": "passed",
        "event_count": int(len(events)),
        "quote_complete_events": int(replay["quote_complete"].sum()),
        "trade_complete_events": int(replay["trade_complete"].sum()),
        "max_session": str(events["date"].max().date()),
        "holdout_rows_loaded": 0,
        "size_unit_warning": (
            "Alpaca SIP quote size fields remain in raw API units; no dollar "
            "capacity claim is made."
        ),
    }
    (args.output_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "command": (
            "python campaigns/CAM-0006/src/run0005.py "
            "--raw-dir campaigns/CAM-0006/artifacts/RUN-0005/raw "
            "--output-dir campaigns/CAM-0006/artifacts/RUN-0005"
        ),
        "resolved_defaults": {
            "candidates": ["all_state", "vol_high"],
            "entry": "first_noncrossed_ask_at_or_after_09:31",
            "exit": "first_noncrossed_bid_at_or_after_calendar_final_exit",
            "additional_slippage_bps_per_side": [0, 2, 5],
        },
        "executed_variant_count": int(len(variants)),
        "max_loaded_date": str(events["date"].max().date()),
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(variants.to_string(index=False))
    print(json.dumps(diagnostics, indent=2))


if __name__ == "__main__":
    main()
