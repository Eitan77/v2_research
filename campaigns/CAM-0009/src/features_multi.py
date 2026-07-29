from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from cam0009 import shifted_rolling_median
from features import add_shifted_beta
from readiness import CUTOFF, HOLDOUT_START, STOCKS


LENGTHS = (1, 10, 15, 30)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_length(
    minutes: pd.DataFrame,
    membership: pd.DataFrame,
    stock_state: pd.DataFrame,
    length: int,
) -> pd.DataFrame:
    frame = minutes.copy()
    frame["bucket_start"] = (
        (frame["minute_number"] - 570) // length * length + 570
    )
    grouped = (
        frame.groupby(["symbol", "date", "bucket_start"], sort=False)
        .agg(
            observed_rows=("minute_number", "size"),
            first_minute=("minute_number", "min"),
            last_minute=("minute_number", "max"),
            formation_open=("open", "first"),
            formation_high=("high", "max"),
            formation_low=("low", "min"),
            formation_close=("close", "last"),
            formation_volume=("volume", "sum"),
            formation_trades=("trade_count", "sum"),
            formation_dollar_volume=("minute_dollar", "sum"),
            session_close_minute=("session_close_minute", "first"),
        )
        .reset_index()
    )
    grouped["formation_complete"] = (
        grouped["observed_rows"].eq(length)
        & grouped["first_minute"].eq(grouped["bucket_start"])
        & grouped["last_minute"].eq(grouped["bucket_start"] + length - 1)
    )
    grouped = grouped[grouped["formation_complete"]].copy()
    grouped["formation_return"] = (
        grouped["formation_close"] / grouped["formation_open"] - 1
    )
    grouped["entry_minute"] = grouped["bucket_start"] + length
    entry = minutes[["symbol", "date", "minute_number", "open"]].rename(
        columns={"minute_number": "entry_minute", "open": "entry_open_raw"}
    )
    grouped = grouped.merge(
        entry,
        on=["symbol", "date", "entry_minute"],
        how="left",
        validate="one_to_one",
    )
    grouped["entry_complete"] = (
        grouped["entry_open_raw"].notna()
        & grouped["entry_minute"].lt(grouped["session_close_minute"])
    )
    grouped["shifted_bucket_dollar_median20"] = grouped.groupby(
        ["symbol", "bucket_start"]
    )["formation_dollar_volume"].transform(
        lambda values: shifted_rolling_median(values, 20, 15)
    )
    grouped["volume_surprise"] = (
        grouped["formation_dollar_volume"]
        / grouped["shifted_bucket_dollar_median20"]
    )
    controls = grouped[grouped["symbol"].isin(["QQQ", "SMH"])][
        ["symbol", "date", "bucket_start", "formation_return"]
    ].pivot(
        index=["date", "bucket_start"],
        columns="symbol",
        values="formation_return",
    ).reset_index().rename(
        columns={"QQQ": "qqq_return", "SMH": "smh_return"}
    )
    stocks = grouped[grouped["symbol"].isin(STOCKS)].merge(
        controls,
        on=["date", "bucket_start"],
        how="left",
        validate="many_to_one",
    ).merge(
        stock_state,
        on=["symbol", "date"],
        how="left",
        validate="many_to_one",
    ).merge(
        membership.assign(pit_member=True),
        on=["symbol", "date"],
        how="left",
        validate="many_to_one",
    )
    stocks["pit_member"] = stocks["pit_member"].fillna(False)
    stocks["raw_residual"] = stocks["formation_return"]
    stocks["qqq_residual"] = (
        stocks["formation_return"]
        - stocks["beta_qqq"] * stocks["qqq_return"]
    )
    stocks["smh_residual"] = (
        stocks["formation_return"]
        - stocks["beta_smh"] * stocks["smh_return"]
    )
    stocks["entry_split"] = stocks["entry_open_raw"] * stocks["split_factor"]
    return stocks.sort_values(["date", "bucket_start", "symbol"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minute", type=Path, required=True)
    parser.add_argument("--membership", type=Path, required=True)
    parser.add_argument("--daily-state", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    minutes = pd.read_parquet(args.minute)
    membership = pd.read_parquet(args.membership)
    daily = pd.read_parquet(args.daily_state)
    minutes["date"] = pd.to_datetime(minutes["date"])
    membership["date"] = pd.to_datetime(membership["date"])
    daily["date"] = pd.to_datetime(daily["date"])
    if max(minutes["date"].max(), daily["date"].max()) > CUTOFF:
        raise RuntimeError("Multi-horizon feature input crosses holdout boundary")
    minutes["minute_dollar"] = minutes["vwap"] * minutes["volume"]

    daily = daily.sort_values(["symbol", "date"]).copy()
    daily["daily_return"] = daily.groupby("symbol")["close_split"].pct_change(
        fill_method=None
    )
    stock_state = daily[daily["symbol"].isin(STOCKS)][
        ["symbol", "date", "prior20_median_dollar_volume", "split_factor"]
    ].merge(
        add_shifted_beta(daily, "QQQ", "qqq"),
        on=["symbol", "date"],
        how="left",
        validate="one_to_one",
    ).merge(
        add_shifted_beta(daily, "SMH", "smh"),
        on=["symbol", "date"],
        how="left",
        validate="one_to_one",
    )

    report = {
        "status": "passed",
        "lengths": {},
        "input_hashes": {
            "minute": sha256(args.minute),
            "membership": sha256(args.membership),
            "daily_state": sha256(args.daily_state),
        },
        "maximum_loaded_date": str(
            max(minutes["date"].max(), daily["date"].max()).date()
        ),
        "holdout_rows_loaded": 0,
    }
    for length in LENGTHS:
        output = args.output_dir / f"formation_{length}m.parquet"
        stocks = build_length(
            minutes,
            membership,
            stock_state,
            length,
        )
        stocks.to_parquet(output, index=False)
        member = stocks["pit_member"]
        report["lengths"][str(length)] = {
            "complete_stock_rows": int(len(stocks)),
            "point_in_time_member_rows": int(member.sum()),
            "entry_complete_member_rows": int(
                (member & stocks["entry_complete"]).sum()
            ),
            "volume_state_complete_member_rows": int(
                (member & stocks["volume_surprise"].notna()).sum()
            ),
            "control_return_missing_member_rows": int(
                (
                    member
                    & (
                        stocks["qqq_return"].isna()
                        | stocks["smh_return"].isna()
                    )
                ).sum()
            ),
            "holdout_rows_loaded": int(
                stocks["date"].ge(HOLDOUT_START).sum()
            ),
            "sha256": sha256(output),
        }
    (args.output_dir / "multi_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
