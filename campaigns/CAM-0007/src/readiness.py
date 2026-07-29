from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cam0007 import (
    CUTOFF,
    canonicalize_news_events,
    is_earnings_release_headline,
    map_announcement_to_session,
)


START = pd.Timestamp("2024-07-01")
LOCAL_NEWS_END = pd.Timestamp("2025-01-02")
ISSUER_GROUPS = ({"GOOG", "GOOGL"}, {"FOX", "FOXA"})


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_symbol_list(value) -> tuple[str | None, bool]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = []
    elif isinstance(value, (list, tuple, np.ndarray)):
        parsed = list(value)
    else:
        parsed = []
    symbols = sorted({str(item).upper() for item in parsed if str(item)})
    if len(symbols) == 1:
        return symbols[0], True
    symbol_set = set(symbols)
    for group in ISSUER_GROUPS:
        if symbol_set and symbol_set.issubset(group):
            preferred = "GOOGL" if "GOOGL" in symbol_set else sorted(symbol_set)[0]
            return preferred, True
    return None, False


def normalize_news(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for item in frame.itertuples(index=False):
        symbol, single_issuer = normalize_symbol_list(item.symbols)
        rows.append(
            {
                "id": item.id,
                "created_at": item.created_at,
                "headline": item.headline,
                "symbol": symbol,
                "single_symbol": single_issuer,
                "source": getattr(item, "source", "benzinga"),
            }
        )
    return pd.DataFrame(rows)


def match_news_to_earnings(
    news_events: pd.DataFrame, earnings: pd.DataFrame, hours: int = 36
) -> pd.Series:
    matches = []
    for item in news_events.itertuples(index=False):
        symbol_earnings = earnings[earnings["symbol"].eq(item.symbol)]
        delta = (symbol_earnings["event_timestamp"] - item.event_timestamp).abs()
        matches.append(bool(len(delta) and delta.min() <= pd.Timedelta(hours=hours)))
    return pd.Series(matches, index=news_events.index)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--remote-news", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(args.catalog), read_only=True)
    try:
        earnings = con.execute(
            """
            SELECT symbol, earnings_datetime, eps_estimate, reported_eps,
                   surprise_pct, source
            FROM earnings
            """
        ).fetch_df()
        local_news = con.execute(
            """
            SELECT id, created_at, headline, symbols, source
            FROM news
            WHERE date BETWEEN DATE '2024-07-01' AND DATE '2025-01-02'
            """
        ).fetch_df()
        calendar = con.execute(
            """
            SELECT DISTINCT try_cast(date AS DATE) AS date, open, close
            FROM calendar
            WHERE try_cast(date AS DATE)
                  BETWEEN DATE '2024-07-01' AND DATE '2026-04-30'
            ORDER BY date
            """
        ).fetch_df()
        membership = con.execute(
            """
            SELECT symbol, try_cast(date AS DATE) AS date
            FROM qqq_pit_membership_daily
            WHERE is_member
              AND try_cast(date AS DATE)
                  BETWEEN DATE '2024-07-01' AND DATE '2026-04-30'
            """
        ).fetch_df()
    finally:
        con.close()
    sessions = pd.to_datetime(calendar["date"])
    membership["date"] = pd.to_datetime(membership["date"])
    membership_keys = set(
        zip(membership["symbol"], membership["date"], strict=True)
    )

    earnings["event_timestamp"] = pd.to_datetime(
        earnings["earnings_datetime"], format="mixed", utc=True
    )
    earnings = earnings[
        earnings["event_timestamp"]
        .dt.tz_convert("America/New_York")
        .dt.tz_localize(None)
        .dt.normalize()
        .between(START, CUTOFF)
    ].copy()
    earnings["event_source"] = "local_earnings_yfinance"
    earnings["source_id"] = (
        earnings["symbol"] + "|" + earnings["earnings_datetime"]
    )

    remote_news = pd.read_parquet(args.remote_news)
    local_normalized = normalize_news(local_news)
    remote_normalized = normalize_news(remote_news)
    combined_news = pd.concat(
        [local_normalized, remote_normalized], ignore_index=True
    ).drop_duplicates(subset=["id"])
    news_events = canonicalize_news_events(combined_news)
    news_events["event_source"] = "alpaca_benzinga_explicit_release"
    news_events["source_id"] = news_events["news_id"].astype(str)

    validation_news = canonicalize_news_events(local_normalized)
    validation_earnings = earnings[
        earnings["event_timestamp"]
        .dt.tz_convert("America/New_York")
        .dt.tz_localize(None)
        .dt.normalize()
        .between(START, LOCAL_NEWS_END)
    ].copy()
    validation_news["ground_truth_match"] = match_news_to_earnings(
        validation_news, validation_earnings
    )
    matched_earnings = 0
    for item in validation_earnings.itertuples(index=False):
        candidates = validation_news[validation_news["symbol"].eq(item.symbol)]
        if len(candidates) and (
            (candidates["event_timestamp"] - item.event_timestamp).abs()
            <= pd.Timedelta(hours=36)
        ).any():
            matched_earnings += 1
    apparent_precision = (
        float(validation_news["ground_truth_match"].mean())
        if len(validation_news)
        else 0.0
    )
    recall = (
        float(matched_earnings / len(validation_earnings))
        if len(validation_earnings)
        else 0.0
    )
    if apparent_precision < 0.95 or recall < 0.80:
        raise RuntimeError(
            f"Detector gate failed: precision={apparent_precision}, recall={recall}"
        )

    news_events["matched_local_earnings"] = match_news_to_earnings(
        news_events, earnings
    )
    unmatched_news = news_events[~news_events["matched_local_earnings"]].copy()
    registry_columns = [
        "symbol",
        "event_timestamp",
        "event_source",
        "source_id",
    ]
    registry = pd.concat(
        [
            earnings[registry_columns],
            unmatched_news[registry_columns],
        ],
        ignore_index=True,
    ).sort_values(["symbol", "event_timestamp"])
    mapped_rows = []
    for item in registry.itertuples(index=False):
        session, bucket = map_announcement_to_session(
            item.event_timestamp, sessions
        )
        mapped_rows.append(
            {
                **item._asdict(),
                "announcement_bucket": bucket,
                "entry_session": session,
            }
        )
    mapped = pd.DataFrame(mapped_rows)
    mapped["entry_session"] = pd.to_datetime(mapped["entry_session"])
    mapped["mapping_status"] = np.where(
        mapped["entry_session"].isna(), mapped["announcement_bucket"], "mapped"
    )
    mapped["membership_eligible"] = [
        (symbol, session) in membership_keys if pd.notna(session) else False
        for symbol, session in zip(
            mapped["symbol"], mapped["entry_session"], strict=True
        )
    ]
    mapped["precutoff_entry"] = mapped["entry_session"].le(CUTOFF)
    eligible = mapped[
        mapped["mapping_status"].eq("mapped")
        & mapped["membership_eligible"]
        & mapped["precutoff_entry"]
    ].copy()
    source_priority = {
        "local_earnings_yfinance": 0,
        "alpaca_benzinga_explicit_release": 1,
    }
    eligible["source_priority"] = eligible["event_source"].map(source_priority)
    eligible = (
        eligible.sort_values(
            ["symbol", "entry_session", "source_priority", "event_timestamp"]
        )
        .drop_duplicates(["symbol", "entry_session"], keep="first")
        .drop(columns=["source_priority"])
        .reset_index(drop=True)
    )
    if eligible.duplicated(["symbol", "entry_session"]).any():
        raise RuntimeError("Duplicate symbol entry session")
    if eligible["entry_session"].max() > CUTOFF:
        raise RuntimeError("Registry maps into sealed entry session")

    validation_news.to_parquet(
        args.output_dir / "detector_validation_events.parquet", index=False
    )
    mapped.to_parquet(args.output_dir / "mapped_event_candidates.parquet", index=False)
    eligible.to_parquet(args.output_dir / "event_registry.parquet", index=False)
    detector_report = {
        "status": "passed",
        "detector": (
            "single issuer tag; explicit Q/FY EPS result with beat/miss/inline/"
            "estimate language or explicit Q earnings revenue/EPS; preview and "
            "guidance-update exclusions; 36-hour symbol clustering"
        ),
        "validation_news_events": int(len(validation_news)),
        "validation_ground_truth_events": int(len(validation_earnings)),
        "matched_news_events": int(
            validation_news["ground_truth_match"].sum()
        ),
        "matched_ground_truth_events": int(matched_earnings),
        "apparent_precision": apparent_precision,
        "ground_truth_recall": recall,
        "precision_caveat": (
            "Unmatched explicit releases may be genuine events missing from the "
            "local yfinance table, so apparent precision is conservative."
        ),
    }
    (args.output_dir / "detector_validation.json").write_text(
        json.dumps(detector_report, indent=2) + "\n", encoding="utf-8"
    )
    attrition = {
        "local_earnings_candidates": int(len(earnings)),
        "combined_unique_news_rows": int(len(combined_news)),
        "explicit_news_event_clusters": int(len(news_events)),
        "news_clusters_matched_to_local_earnings": int(
            news_events["matched_local_earnings"].sum()
        ),
        "unmatched_news_clusters_added": int(len(unmatched_news)),
        "combined_candidates_before_mapping": int(len(mapped)),
        "during_session_excluded": int(
            mapped["mapping_status"].eq("during_session_excluded").sum()
        ),
        "unmapped_session": int(
            mapped["mapping_status"].eq("unmapped_session").sum()
        ),
        "not_point_in_time_qqq": int(
            (
                mapped["mapping_status"].eq("mapped")
                & ~mapped["membership_eligible"]
            ).sum()
        ),
        "actionable_entry_after_cutoff": int(
            (
                mapped["mapping_status"].eq("mapped")
                & mapped["membership_eligible"]
                & ~mapped["precutoff_entry"]
            ).sum()
        ),
        "eligible_registry_events": int(len(eligible)),
        "eligible_symbols": int(eligible["symbol"].nunique()),
        "minimum_entry_session": str(eligible["entry_session"].min().date()),
        "maximum_entry_session": str(eligible["entry_session"].max().date()),
        "source_counts": eligible["event_source"].value_counts().to_dict(),
        "announcement_bucket_counts": eligible[
            "announcement_bucket"
        ].value_counts().to_dict(),
        "monthly_counts": eligible.assign(
            month=eligible["entry_session"].dt.to_period("M").astype(str)
        )["month"].value_counts().sort_index().to_dict(),
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "event_attrition.json").write_text(
        json.dumps(attrition, indent=2) + "\n", encoding="utf-8"
    )
    hashes = {
        path.name: sha256(path)
        for path in args.output_dir.iterdir()
        if path.is_file()
    }
    (args.output_dir / "hashes.json").write_text(
        json.dumps(hashes, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(detector_report, indent=2))
    print(json.dumps(attrition, indent=2))


if __name__ == "__main__":
    main()
