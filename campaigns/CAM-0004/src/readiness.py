from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import exchange_calendars as xcals

from cam0004 import (
    FEATURES,
    build_daily_features,
    load_membership,
    load_regular_30m,
    load_split_daily,
    stable_frame_hash,
    validate_cutoff,
)


EXACT_SOURCE_FIELDS = [
    "accruals",
    "asset_growth",
    "book_to_market",
    "composite_equity_issuance",
    "failure_probability",
    "gross_profitability",
    "investment_to_assets",
    "momentum",
    "net_operating_assets",
    "net_stock_issues",
    "o_score",
    "return_on_assets",
    "beta",
    "short_term_reversal",
    "size",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--daily-cache", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    membership = load_membership()
    bars = load_regular_30m()
    calendar = xcals.get_calendar("XNYS")
    sessions = pd.DatetimeIndex(
        calendar.sessions_in_range("2024-05-01", "2026-04-30")
    ).tz_localize(None)
    session_membership = membership[membership["date"].isin(sessions)].copy()
    member_symbols = sorted(membership["symbol"].unique())
    if args.daily_cache:
        daily = pd.read_parquet(args.daily_cache)
        daily["date"] = pd.to_datetime(daily["date"])
        split_report = {
            "source": "cutoff-bounded Alpaca split-adjusted daily cache",
            "path": str(args.daily_cache),
            "rows": int(len(daily)),
        }
    else:
        daily, split_report = load_split_daily(member_symbols)
    features = build_daily_features(daily)
    validate_cutoff(membership)
    validate_cutoff(bars)
    validate_cutoff(daily)
    validate_cutoff(features)

    membership_keys = session_membership[["date", "symbol"]].drop_duplicates()
    coverage = (
        bars.groupby(["date", "symbol"])
        .agg(
            observed_bars=("bar_start_ts", "size"),
            first_start=("bar_start_ts", "min"),
            last_end=("bar_end_ts", "max"),
            null_ohlc=("open", lambda x: int(x.isna().sum())),
        )
        .reset_index()
    )
    coverage = membership_keys.merge(
        coverage, on=["date", "symbol"], how="left", validate="one_to_one"
    )
    coverage["observed_bars"] = coverage["observed_bars"].fillna(0).astype(int)
    coverage["complete_13"] = coverage["observed_bars"] == 13
    feature_coverage = membership_keys.merge(
        features, on=["date", "symbol"], how="left", validate="one_to_one"
    )
    feature_coverage["complete_proxy_features"] = feature_coverage[FEATURES].notna().all(axis=1)

    known = session_membership["known_at_ts"]
    member_date = pd.to_datetime(session_membership["date"]).dt.tz_localize(
        "America/New_York"
    )
    known_before_session = known < member_date + pd.Timedelta(hours=9, minutes=30)
    report = {
        "status": "passed_for_labeled_adapted_mechanism_only",
        "source_faithful_replication_status": "blocked",
        "source_faithful_blockers": {
            "missing_exact_point_in_time_sp500_membership": True,
            "missing_exact_causally_lagged_characteristics": EXACT_SOURCE_FIELDS,
            "open_source_characteristics_not_loaded": (
                "Public Open Source Asset Pricing files use CRSP PERMNO and do not "
                "provide a declared, license-clean symbol mapping in this workspace; "
                "the current exact source data cannot be joined causally."
            ),
        },
        "adapted_mechanism": {
            "universe": "point-in-time QQQ members",
            "features": FEATURES,
            "label": "price/liquidity proxy residual; not source replication",
        },
        "max_loaded_date": max(
            str(membership["date"].max().date()),
            str(bars["date"].max().date()),
            str(daily["date"].max().date()),
        ),
        "holdout_rows_loaded": 0,
        "membership": {
            "rows": int(len(membership)),
            "trading_session_rows": int(len(session_membership)),
            "calendar_rows_excluded": int(len(membership) - len(session_membership)),
            "dates": int(membership["date"].nunique()),
            "symbols": int(membership["symbol"].nunique()),
            "min_date": str(membership["date"].min().date()),
            "max_date": str(membership["date"].max().date()),
            "known_before_session_rows": int(known_before_session.sum()),
            "known_after_or_at_session_rows": int((~known_before_session).sum()),
        },
        "regular_30m_bars": {
            "rows": int(len(bars)),
            "dates": int(bars["date"].nunique()),
            "symbols": int(bars["symbol"].nunique()),
            "min_date": str(bars["date"].min().date()),
            "max_date": str(bars["date"].max().date()),
            "member_symbol_dates": int(len(coverage)),
            "complete_13_symbol_dates": int(coverage["complete_13"].sum()),
            "incomplete_symbol_dates": int((~coverage["complete_13"]).sum()),
            "zero_bar_symbol_dates": int((coverage["observed_bars"] == 0).sum()),
            "null_ohlc_rows": int(bars[["open", "high", "low", "close"]].isna().any(axis=1).sum()),
        },
        "daily_split_adjustment": split_report,
        "proxy_feature_coverage": {
            "member_symbol_dates": int(len(feature_coverage)),
            "complete_rows": int(feature_coverage["complete_proxy_features"].sum()),
            "incomplete_rows": int((~feature_coverage["complete_proxy_features"]).sum()),
            "attrition_fraction": float(
                1.0 - feature_coverage["complete_proxy_features"].mean()
            ),
        },
        "hashes": {
            "membership": stable_frame_hash(membership, ["date", "symbol"]),
            "bars": stable_frame_hash(bars, ["date", "symbol", "bar_start_ts"]),
            "daily": stable_frame_hash(daily, ["date", "symbol"]),
            "features": stable_frame_hash(features, ["date", "symbol"]),
        },
    }
    if report["max_loaded_date"] > "2026-04-30":
        raise RuntimeError("holdout cutoff failed")
    if report["membership"]["known_after_or_at_session_rows"]:
        raise RuntimeError("membership availability timing failed")
    if report["regular_30m_bars"]["null_ohlc_rows"]:
        raise RuntimeError("bar OHLC readiness failed")
    if report["proxy_feature_coverage"]["complete_rows"] < 10000:
        raise RuntimeError(
            "adapted feature readiness failed: insufficient complete rows"
        )

    membership.to_parquet(args.output_dir / "membership.parquet", index=False)
    bars.to_parquet(args.output_dir / "regular_30m_bars.parquet", index=False)
    daily.to_parquet(args.output_dir / "daily_split_adjusted.parquet", index=False)
    features.to_parquet(args.output_dir / "proxy_features.parquet", index=False)
    coverage.to_parquet(args.output_dir / "bar_coverage.parquet", index=False)
    feature_coverage.to_parquet(
        args.output_dir / "feature_coverage.parquet", index=False
    )
    (args.output_dir / "readiness.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
