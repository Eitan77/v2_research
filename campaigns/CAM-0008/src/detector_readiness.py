from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cam0008 import CUTOFF, map_event_clock, parse_action


START = pd.Timestamp("2024-07-01")
REMOTE_START = pd.Timestamp("2025-01-03")
KEYWORD = r"(?i)\b(?:upgrades?|downgrades?|initiates?|price target)\b"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_symbols(value: object) -> list[str]:
    if isinstance(value, (list, tuple, np.ndarray)):
        parsed = list(value)
    else:
        try:
            parsed = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    return sorted({str(item).upper() for item in parsed if str(item).strip()})


def deduplicate(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    rows = []
    removed = 0
    for _, group in frame.sort_values("created_at").groupby(
        ["symbol", "firm", "action_type"], dropna=False
    ):
        last = None
        for row in group.itertuples(index=False):
            if last is not None and row.created_at - last <= pd.Timedelta(hours=24):
                removed += 1
                continue
            rows.append(row._asdict())
            last = row.created_at
    return pd.DataFrame(rows), removed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--remote-news", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(args.catalog), read_only=True)
    try:
        local = con.execute(
            """
            SELECT id, created_at, headline, symbols, source, url
            FROM news
            WHERE try_cast(created_at AS TIMESTAMP)
                  >= TIMESTAMP '2024-07-01 00:00:00'
              AND try_cast(created_at AS TIMESTAMP)
                  < TIMESTAMP '2025-01-03 05:00:00'
            """
        ).fetch_df()
        membership = con.execute(
            """
            SELECT try_cast(date AS DATE) AS date, symbol
            FROM qqq_pit_membership_daily
            WHERE is_member
              AND try_cast(date AS DATE)
                  BETWEEN DATE '2024-07-01' AND DATE '2026-04-30'
            """
        ).fetch_df()
        calendar = con.execute(
            """
            SELECT DISTINCT try_cast(date AS DATE) AS date
            FROM calendar
            WHERE try_cast(date AS DATE)
                  BETWEEN DATE '2024-07-01' AND DATE '2026-04-30'
            ORDER BY date
            """
        ).fetch_df()
    finally:
        con.close()
    remote = pd.read_parquet(args.remote_news)[
        ["id", "created_at", "headline", "symbols", "source", "url"]
    ].copy()
    local["origin"] = "catalog_news"
    remote["origin"] = "targeted_alpaca_news"
    combined = (
        pd.concat([local, remote], ignore_index=True)
        .drop_duplicates("id")
        .copy()
    )
    combined["created_at"] = pd.to_datetime(combined["created_at"], utc=True)
    local_time = combined["created_at"].dt.tz_convert("America/New_York")
    combined = combined[
        local_time.ge(pd.Timestamp("2024-07-01", tz="America/New_York"))
        & local_time.lt(pd.Timestamp("2026-05-01", tz="America/New_York"))
    ].copy()
    combined["parsed_symbols"] = combined["symbols"].map(parse_symbols)
    combined["symbols"] = combined["parsed_symbols"].map(json.dumps)
    combined["single_symbol"] = combined["parsed_symbols"].map(len).eq(1)
    combined["symbol"] = combined["parsed_symbols"].map(
        lambda values: values[0] if len(values) == 1 else None
    )
    union = set(membership["symbol"].unique())
    combined = combined[combined["symbol"].isin(union)].copy()
    combined["parsed_action"] = combined["headline"].map(parse_action)
    detected = combined[
        combined["single_symbol"] & combined["parsed_action"].notna()
    ].copy()
    for column in ("action_type", "action_sign", "rating", "firm"):
        detected[column] = detected["parsed_action"].map(
            lambda value: value[column]
        )
    empty_firm = detected["firm"].fillna("").str.len().lt(2)
    detected = detected[~empty_firm].copy()
    before_dedup = len(detected)
    detected, deduplicated = deduplicate(detected)
    sessions = pd.DatetimeIndex(pd.to_datetime(calendar["date"]))
    clocks = detected["created_at"].map(lambda value: map_event_clock(value, sessions))
    clock_frame = pd.DataFrame(clocks.tolist())
    detected = pd.concat(
        [detected.reset_index(drop=True), clock_frame], axis=1
    )
    membership["date"] = pd.to_datetime(membership["date"])
    detected = detected.merge(
        membership.assign(membership_eligible=True),
        left_on=["symbol", "entry_session"],
        right_on=["symbol", "date"],
        how="left",
        validate="many_to_one",
    ).drop(columns=["date"])
    detected["membership_eligible"] = detected[
        "membership_eligible"
    ].fillna(False)
    detected["precutoff_entry"] = detected["entry_session"].le(CUTOFF)

    rng = np.random.default_rng(8008)
    audit_parts = []
    for _, group in detected.groupby("action_type"):
        size = min(15, len(group))
        if size:
            audit_parts.append(
                group.iloc[rng.choice(len(group), size=size, replace=False)]
            )
    detected_audit = pd.concat(audit_parts, ignore_index=True)
    detected_audit["detector_label"] = "detected"
    rejected = combined[
        combined["single_symbol"]
        & combined["headline"].fillna("").str.contains(KEYWORD, regex=True)
        & combined["parsed_action"].isna()
    ].copy()
    rejected_size = min(40, len(rejected))
    rejected_audit = rejected.iloc[
        rng.choice(len(rejected), size=rejected_size, replace=False)
    ].copy()
    rejected_audit["detector_label"] = "rejected"
    audit_columns = [
        "id",
        "created_at",
        "symbol",
        "headline",
        "detector_label",
        "origin",
    ]
    audit = pd.concat(
        [detected_audit[audit_columns], rejected_audit[audit_columns]],
        ignore_index=True,
    ).sort_values(["detector_label", "id"])
    audit["manual_valid"] = ""
    audit["manual_note"] = ""

    detected = detected.drop(columns=["parsed_action", "parsed_symbols"])
    detected.to_parquet(
        args.output_dir / "detected_candidates.parquet", index=False
    )
    combined.drop(columns=["parsed_action", "parsed_symbols"]).to_parquet(
        args.output_dir / "combined_scoped_news.parquet", index=False
    )
    audit.to_csv(args.output_dir / "frozen_audit_sample.csv", index=False)
    report = {
        "status": "awaiting_manual_audit",
        "combined_scoped_news_rows": int(len(combined)),
        "combined_symbols": int(combined["symbol"].nunique()),
        "single_symbol_rows": int(combined["single_symbol"].sum()),
        "detected_before_empty_firm_and_dedup": int(before_dedup),
        "empty_firm_excluded": int(empty_firm.sum()),
        "same_firm_symbol_action_24h_duplicates_removed": int(deduplicated),
        "detected_events": int(len(detected)),
        "action_type_counts": {
            str(key): int(value)
            for key, value in detected["action_type"].value_counts().items()
        },
        "mapping_status_counts": {
            str(key): int(value)
            for key, value in detected["mapping_status"].value_counts().items()
        },
        "membership_eligible_events": int(
            detected["membership_eligible"].sum()
        ),
        "precutoff_actionable_membership_events": int(
            (
                detected["membership_eligible"]
                & detected["precutoff_entry"]
                & detected["mapping_status"].eq("actionable")
            ).sum()
        ),
        "audit_rows": int(len(audit)),
        "audit_detected_rows": int(audit["detector_label"].eq("detected").sum()),
        "audit_rejected_rows": int(audit["detector_label"].eq("rejected").sum()),
        "minimum_created_at": str(combined["created_at"].min()),
        "maximum_created_at": str(combined["created_at"].max()),
        "maximum_entry_session": str(detected["entry_session"].max()),
        "holdout_rows_loaded": int(
            local_time.loc[combined.index].ge(
                pd.Timestamp("2026-05-01", tz="America/New_York")
            ).sum()
        ),
    }
    (args.output_dir / "detector_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    hashes = {
        path.name: sha256(path)
        for path in (
            args.output_dir / "detected_candidates.parquet",
            args.output_dir / "combined_scoped_news.parquet",
            args.output_dir / "frozen_audit_sample.csv",
            args.output_dir / "detector_report.json",
        )
    }
    (args.output_dir / "hashes.json").write_text(
        json.dumps(hashes, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
