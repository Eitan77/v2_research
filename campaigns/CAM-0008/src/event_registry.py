from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import pandas as pd

from cam0008 import CUTOFF, map_event_clock


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cluster_actions(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    episodes = []
    conflict_episodes = 0
    same_sign_rows_collapsed = 0
    for symbol, group in frame.sort_values("created_at").groupby("symbol"):
        cluster = []
        previous = None
        for row in group.itertuples(index=False):
            if previous is None or row.created_at - previous <= pd.Timedelta(minutes=30):
                cluster.append(row)
            else:
                signs = {int(item.action_sign) for item in cluster}
                if len(signs) == 1:
                    episodes.append((symbol, cluster))
                    same_sign_rows_collapsed += len(cluster) - 1
                else:
                    conflict_episodes += 1
                cluster = [row]
            previous = row.created_at
        if cluster:
            signs = {int(item.action_sign) for item in cluster}
            if len(signs) == 1:
                episodes.append((symbol, cluster))
                same_sign_rows_collapsed += len(cluster) - 1
            else:
                conflict_episodes += 1
    rows = []
    for symbol, cluster in episodes:
        first = cluster[0]
        rows.append(
            {
                "symbol": symbol,
                "event_timestamp": first.created_at,
                "action_sign": int(first.action_sign),
                "primary_action_type": first.action_type,
                "action_types": json.dumps(
                    sorted({str(item.action_type) for item in cluster})
                ),
                "firms": json.dumps(
                    sorted({str(item.firm) for item in cluster})
                ),
                "firm_count": len({str(item.firm) for item in cluster}),
                "headline_count": len(cluster),
                "primary_headline": first.headline,
                "source_ids": json.dumps(
                    [str(item.id) for item in cluster]
                ),
                "origins": json.dumps(
                    sorted({str(item.origin) for item in cluster})
                ),
            }
        )
    return pd.DataFrame(rows), {
        "conflicting_sign_episodes_excluded": conflict_episodes,
        "same_sign_rows_collapsed": same_sign_rows_collapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--detected-candidates", type=Path, required=True)
    parser.add_argument("--earnings-registry", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidates = pd.read_parquet(args.detected_candidates)
    candidates["created_at"] = pd.to_datetime(candidates["created_at"], utc=True)
    actionable = candidates[
        candidates["mapping_status"].eq("actionable")
        & candidates["membership_eligible"]
        & candidates["precutoff_entry"]
    ].copy()
    episodes, cluster_report = cluster_actions(actionable)

    con = duckdb.connect(str(args.catalog), read_only=True)
    try:
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
            SELECT DISTINCT try_cast(date AS DATE) AS date, close
            FROM calendar
            WHERE try_cast(date AS DATE)
                  BETWEEN DATE '2024-07-01' AND DATE '2026-04-30'
            ORDER BY date
            """
        ).fetch_df()
    finally:
        con.close()
    sessions = pd.DatetimeIndex(pd.to_datetime(calendar["date"]))
    session_closes = {
        pd.Timestamp(row.date): str(row.close)
        for row in calendar.itertuples(index=False)
    }
    clocks = episodes["event_timestamp"].map(
        lambda value: map_event_clock(value, sessions, session_closes)
    )
    episodes = pd.concat(
        [episodes.reset_index(drop=True), pd.DataFrame(clocks.tolist())],
        axis=1,
    )
    membership["date"] = pd.to_datetime(membership["date"])
    episodes = episodes.merge(
        membership.assign(membership_eligible=True),
        left_on=["symbol", "entry_session"],
        right_on=["symbol", "date"],
        how="left",
        validate="many_to_one",
    ).drop(columns=["date"])
    episodes["membership_eligible"] = episodes[
        "membership_eligible"
    ].fillna(False)
    episodes["precutoff_entry"] = episodes["entry_session"].le(CUTOFF)

    earnings = pd.read_parquet(args.earnings_registry)[
        ["symbol", "event_timestamp"]
    ].rename(columns={"event_timestamp": "earnings_timestamp"})
    earnings["earnings_timestamp"] = pd.to_datetime(
        earnings["earnings_timestamp"], utc=True
    )
    episodes = pd.merge_asof(
        episodes.sort_values("event_timestamp"),
        earnings.sort_values("earnings_timestamp"),
        left_on="event_timestamp",
        right_on="earnings_timestamp",
        by="symbol",
        direction="backward",
        tolerance=pd.Timedelta(hours=36),
    )
    episodes["within_36h_after_earnings"] = episodes[
        "earnings_timestamp"
    ].notna()
    registry = episodes[
        episodes["mapping_status"].eq("actionable")
        & episodes["membership_eligible"]
        & episodes["precutoff_entry"]
    ].copy()
    registry = registry.sort_values(
        ["entry_session", "entry_minute", "symbol", "event_timestamp"]
    ).reset_index(drop=True)
    if registry.duplicated(
        ["symbol", "event_timestamp"]
    ).any():
        raise RuntimeError("Duplicate analyst event episode")
    if registry["entry_session"].max() > CUTOFF:
        raise RuntimeError("Analyst registry crosses sealed boundary")
    output = args.output_dir / "event_registry.parquet"
    registry.to_parquet(output, index=False)
    report = {
        "status": "passed",
        "detected_candidates": int(len(candidates)),
        "actionable_membership_candidates": int(len(actionable)),
        "episodes_before_remap": int(len(episodes)),
        **cluster_report,
        "eligible_event_episodes": int(len(registry)),
        "eligible_symbols": int(registry["symbol"].nunique()),
        "action_sign_counts": {
            str(key): int(value)
            for key, value in registry["action_sign"].value_counts().items()
        },
        "primary_action_type_counts": {
            str(key): int(value)
            for key, value in registry["primary_action_type"].value_counts().items()
        },
        "release_bucket_counts": {
            str(key): int(value)
            for key, value in registry["release_bucket"].value_counts().items()
        },
        "earnings_confound_events": int(
            registry["within_36h_after_earnings"].sum()
        ),
        "multi_firm_episodes": int(registry["firm_count"].gt(1).sum()),
        "minimum_event_timestamp": str(registry["event_timestamp"].min()),
        "maximum_event_timestamp": str(registry["event_timestamp"].max()),
        "maximum_entry_session": str(registry["entry_session"].max().date()),
        "holdout_rows_loaded": 0,
        "earnings_control_source": str(args.earnings_registry),
        "earnings_control_note": "Revalidated V2 data artifact used only as a causal confound flag, not as inherited strategy evidence."
    }
    (args.output_dir / "event_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    hashes = {
        "event_registry.parquet": sha256(output),
        "event_report.json": sha256(args.output_dir / "event_report.json"),
        "earnings_registry_input": sha256(args.earnings_registry),
    }
    (args.output_dir / "hashes.json").write_text(
        json.dumps(hashes, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
