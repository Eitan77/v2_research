from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0009 import shifted_rolling_median
from readiness import CUTOFF, HOLDOUT_START, STOCKS


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_shifted_beta(
    daily: pd.DataFrame, control_symbol: str, label: str
) -> pd.DataFrame:
    control = daily[daily["symbol"].eq(control_symbol)][
        ["date", "daily_return"]
    ].rename(columns={"daily_return": "control_return"})
    pieces = []
    for symbol, frame in daily[daily["symbol"].isin(STOCKS)].groupby("symbol"):
        group = frame.merge(control, on="date", how="left").sort_values("date")
        covariance = group["daily_return"].rolling(
            60, min_periods=40
        ).cov(group["control_return"])
        variance = group["control_return"].rolling(
            60, min_periods=40
        ).var()
        group[f"beta_{label}"] = (covariance / variance).shift(1)
        pieces.append(group[["symbol", "date", f"beta_{label}"]])
    return pd.concat(pieces, ignore_index=True)


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
        raise RuntimeError("Feature input crosses holdout boundary")

    minutes["bucket_start"] = (
        (minutes["minute_number"] - 570) // 5 * 5 + 570
    )
    grouped = (
        minutes.groupby(["symbol", "date", "bucket_start"], sort=False)
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
            session_close_minute=("session_close_minute", "first"),
        )
        .reset_index()
    )
    dollar = (
        minutes.assign(dollar=lambda frame: frame["vwap"] * frame["volume"])
        .groupby(["symbol", "date", "bucket_start"], sort=False)["dollar"]
        .sum()
        .rename("formation_dollar_volume_exact")
        .reset_index()
    )
    grouped = grouped.merge(
        dollar,
        on=["symbol", "date", "bucket_start"],
        how="left",
        validate="one_to_one",
    ).rename(
        columns={
            "formation_dollar_volume_exact": "formation_dollar_volume"
        }
    )
    grouped["formation_complete"] = (
        grouped["observed_rows"].eq(5)
        & grouped["first_minute"].eq(grouped["bucket_start"])
        & grouped["last_minute"].eq(grouped["bucket_start"] + 4)
    )
    grouped = grouped[grouped["formation_complete"]].copy()
    grouped["formation_return"] = (
        grouped["formation_close"] / grouped["formation_open"] - 1
    )
    grouped["entry_minute"] = grouped["bucket_start"] + 5
    entry = minutes[
        ["symbol", "date", "minute_number", "open"]
    ].rename(
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

    daily = daily.sort_values(["symbol", "date"]).copy()
    daily["daily_return"] = daily.groupby("symbol")["close_split"].pct_change(
        fill_method=None
    )
    beta_qqq = add_shifted_beta(daily, "QQQ", "qqq")
    beta_smh = add_shifted_beta(daily, "SMH", "smh")
    stock_state = daily[daily["symbol"].isin(STOCKS)][
        [
            "symbol",
            "date",
            "prior20_median_dollar_volume",
            "split_factor",
        ]
    ].merge(
        beta_qqq, on=["symbol", "date"], how="left", validate="one_to_one"
    ).merge(
        beta_smh, on=["symbol", "date"], how="left", validate="one_to_one"
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
    stocks = grouped[grouped["symbol"].isin(STOCKS)].copy()
    stocks = stocks.merge(
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
    stocks["entry_split"] = (
        stocks["entry_open_raw"] * stocks["split_factor"]
    )
    output = args.output_dir / "formation_5m.parquet"
    stocks.sort_values(
        ["date", "bucket_start", "symbol"]
    ).to_parquet(output, index=False)

    attrition = {
        "complete_five_minute_stock_rows": int(len(stocks)),
        "point_in_time_member_rows": int(stocks["pit_member"].sum()),
        "entry_complete_member_rows": int(
            (stocks["pit_member"] & stocks["entry_complete"]).sum()
        ),
        "liquidity_state_complete_member_rows": int(
            (
                stocks["pit_member"]
                & stocks["prior20_median_dollar_volume"].notna()
            ).sum()
        ),
        "volume_state_complete_member_rows": int(
            (
                stocks["pit_member"]
                & stocks["volume_surprise"].notna()
            ).sum()
        ),
        "qqq_beta_complete_member_rows": int(
            (stocks["pit_member"] & stocks["beta_qqq"].notna()).sum()
        ),
        "smh_beta_complete_member_rows": int(
            (stocks["pit_member"] & stocks["beta_smh"].notna()).sum()
        ),
        "control_return_missing_member_rows": int(
            (
                stocks["pit_member"]
                & (
                    stocks["qqq_return"].isna()
                    | stocks["smh_return"].isna()
                )
            ).sum()
        ),
        "maximum_loaded_date": str(
            max(minutes["date"].max(), daily["date"].max()).date()
        ),
        "holdout_rows_loaded": int(
            stocks["date"].ge(HOLDOUT_START).sum()
        ),
    }
    report = {
        "status": "passed",
        "attrition": attrition,
        "hashes": {
            "formation_5m": sha256(output),
            "minute_input": sha256(args.minute),
            "membership_input": sha256(args.membership),
            "daily_state_input": sha256(args.daily_state),
        },
    }
    (args.output_dir / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
