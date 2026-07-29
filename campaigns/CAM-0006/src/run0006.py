from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from cam0006 import CUTOFF
from run0005 import replay_events, summarize


def merge_raw(original: pd.DataFrame, extension: pd.DataFrame) -> pd.DataFrame:
    frame = pd.concat([original, extension], ignore_index=True)
    identity = [
        column
        for column in (
            "event_id",
            "phase",
            "t",
            "x",
            "p",
            "s",
            "ap",
            "bp",
            "as",
            "bs",
            "z",
        )
        if column in frame.columns
    ]
    return frame.drop_duplicates(subset=identity)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run0005-dir", type=Path, required=True)
    parser.add_argument("--extension-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    events = pd.read_parquet(args.run0005_dir / "raw" / "events.parquet")
    original_quotes = pd.read_parquet(
        args.run0005_dir / "raw" / "quotes.parquet"
    )
    original_trades = pd.read_parquet(
        args.run0005_dir / "raw" / "trades.parquet"
    )
    extension_quotes = pd.read_parquet(args.extension_dir / "quotes.parquet")
    extension_trades = pd.read_parquet(args.extension_dir / "trades.parquet")
    events["date"] = pd.to_datetime(events["date"])
    if events["date"].max() > CUTOFF:
        raise RuntimeError("Sealed event loaded")
    quotes = merge_raw(original_quotes, extension_quotes)
    trades = merge_raw(original_trades, extension_trades)
    replay = replay_events(events, quotes, trades)
    variants, monthly, blocks = summarize(replay)
    if len(variants) != 6:
        raise RuntimeError(f"Expected 6 variants, got {len(variants)}")
    prior = pd.read_csv(args.run0005_dir / "variants.csv")
    comparison = variants.merge(
        prior[
            [
                "variant",
                "full_net_simple_return",
                "average_month_15m",
                "standard_max_drawdown",
                "event_count",
            ]
        ],
        on="variant",
        suffixes=("_extended", "_ten_second"),
        validate="one_to_one",
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    replay.to_parquet(args.output_dir / "event_replay.parquet", index=False)
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    blocks.to_csv(args.output_dir / "blocks.csv", index=False)
    comparison.to_csv(args.output_dir / "comparison.csv", index=False)
    complete = replay[replay["quote_complete"]]
    diagnostics = {
        "status": "passed",
        "event_count": int(len(events)),
        "quote_complete_events": int(replay["quote_complete"].sum()),
        "trade_complete_events": int(replay["trade_complete"].sum()),
        "late_entry_quote_events_over_10s": int(
            complete["entry_delay_ms"].gt(10_000).sum()
        ),
        "late_exit_quote_events_over_10s": int(
            complete["exit_delay_ms"].gt(10_000).sum()
        ),
        "maximum_entry_delay_ms": float(complete["entry_delay_ms"].max()),
        "maximum_exit_delay_ms": float(complete["exit_delay_ms"].max()),
        "max_session": str(events["date"].max().date()),
        "holdout_rows_loaded": 0,
        "interpretation": (
            "Quotes after 10 seconds are actual delayed fills and do not "
            "establish 09:31 or final-target immediacy."
        ),
    }
    (args.output_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "command": (
            "python campaigns/CAM-0006/src/run0006.py "
            "--run0005-dir campaigns/CAM-0006/artifacts/RUN-0005 "
            "--extension-dir campaigns/CAM-0006/artifacts/RUN-0006/raw "
            "--output-dir campaigns/CAM-0006/artifacts/RUN-0006"
        ),
        "resolved_defaults": {
            "candidates": ["all_state", "vol_high"],
            "extension_seconds": 120,
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
    print(comparison.to_string(index=False))
    print(json.dumps(diagnostics, indent=2))


if __name__ == "__main__":
    main()
