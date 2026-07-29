from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from cam0007 import CUTOFF, max_drawdown_and_recovery
from run0003 import make_candidates
from run0004 import simulate_allocated


PROFILES = {
    "recent_best": {
        "source_dir": "RUN-0003",
        "source_variant": "combined__p4_n8__1001__10bp__cap50",
        "rule": "equal_cohort",
        "positive_horizon": 4,
        "negative_horizon": 8,
        "entry_minute": "10:01",
        "cost": 10,
        "cap": 0.50,
    },
    "full_history_stable": {
        "source_dir": "RUN-0003",
        "source_variant": "combined__p5_n10__1001__10bp__cap50",
        "rule": "equal_cohort",
        "positive_horizon": 5,
        "negative_horizon": 10,
        "entry_minute": "10:01",
        "cost": 10,
        "cap": 0.50,
    },
    "lower_concentration": {
        "source_dir": "RUN-0004",
        "source_variant": "strength_priority__p9_n7__1001__10bp__cap33",
        "rule": "strength_priority",
        "positive_horizon": 9,
        "negative_horizon": 7,
        "entry_minute": "10:01",
        "cost": 10,
        "cap": 0.33,
    },
}
SEED = 7005
REPLICATIONS = 20_000
BLOCK_MONTHS = 3


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixed_months(daily: pd.DataFrame, start: str) -> pd.Series:
    periods = pd.period_range(start=start, end="2026-04", freq="M")
    values = (
        daily.assign(month=daily["date"].dt.to_period("M"))
        .groupby("month")["net_pnl"]
        .sum()
    )
    return values.reindex(periods, fill_value=0.0)


def bootstrap_months(
    values: np.ndarray, rng: np.random.Generator
) -> dict:
    n = len(values)
    blocks = math.ceil(n / BLOCK_MONTHS)
    starts = rng.integers(0, n, size=(REPLICATIONS, blocks))
    indices = (
        starts[:, :, None] + np.arange(BLOCK_MONTHS)[None, None, :]
    ) % n
    samples = values[indices].reshape(REPLICATIONS, -1)[:, :n]
    averages = samples.mean(axis=1)
    return {
        "months": n,
        "replications": REPLICATIONS,
        "block_months": BLOCK_MONTHS,
        "average_month_q05": float(np.quantile(averages, 0.05)),
        "average_month_median": float(np.quantile(averages, 0.50)),
        "average_month_q95": float(np.quantile(averages, 0.95)),
        "probability_average_month_positive": float(np.mean(averages > 0)),
        "probability_average_month_at_least_10pct": float(
            np.mean(averages >= 0.10)
        ),
    }


def rolling_report(months: pd.Series) -> dict:
    result = {}
    for window in (3, 6, 12):
        rolling = months.rolling(window, min_periods=window).mean().dropna()
        result[str(window)] = {
            "worst_average_month": float(rolling.min()),
            "worst_end_month": str(rolling.idxmin()),
            "best_average_month": float(rolling.max()),
            "best_end_month": str(rolling.idxmax()),
        }
    return result


def compact_metrics(daily: pd.DataFrame) -> dict:
    full = fixed_months(daily, "2024-07")
    recent = fixed_months(daily, "2025-02")
    drawdown, recovery, unresolved = max_drawdown_and_recovery(daily)
    return {
        "full_average_month": float(full.mean()),
        "recent_15m_average_month": float(recent.mean()),
        "recent_15m_positive_months": int(recent.gt(0).sum()),
        "recent_15m_negative_months": int(recent.lt(0).sum()),
        "recent_15m_inactive_months": int(recent.eq(0).sum()),
        "maximum_drawdown": drawdown,
        "recovery_days": recovery,
        "recovery_unresolved": unresolved,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record", type=Path, required=True)
    parser.add_argument("--event-readiness", type=Path, required=True)
    parser.add_argument("--event-minutes", type=Path, required=True)
    parser.add_argument("--causal-features", type=Path, required=True)
    parser.add_argument("--daily-split", type=Path, required=True)
    parser.add_argument("--source-artifacts-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    record = yaml.safe_load(args.run_record.read_text(encoding="utf-8"))
    if record["status"] != "frozen":
        raise RuntimeError("RUN-0005 record is not frozen")

    readiness = pd.read_parquet(args.event_readiness)
    minutes = pd.read_parquet(args.event_minutes)
    features = pd.read_parquet(args.causal_features)
    daily = pd.read_parquet(args.daily_split)
    readiness["entry_session"] = pd.to_datetime(readiness["entry_session"])
    minutes["date"] = pd.to_datetime(minutes["date"])
    daily["date"] = pd.to_datetime(daily["date"])
    if max(
        readiness["entry_session"].max(),
        minutes["date"].max(),
        daily["date"].max(),
    ) > CUTOFF:
        raise RuntimeError("RUN-0005 input crosses sealed boundary")
    frame = readiness.merge(
        features[
            [
                "symbol",
                "event_timestamp",
                "stock_vol20",
                "stock_vol_prior60_median",
                "stock_vol_high",
            ]
        ],
        on=["symbol", "event_timestamp"],
        how="left",
        validate="one_to_one",
    )
    entry = minutes[minutes["minute"].eq("10:01")][
        ["symbol", "date", "open"]
    ].rename(columns={"open": "entry_1001_raw"})
    frame = frame.merge(
        entry,
        left_on=["symbol", "entry_session"],
        right_on=["symbol", "date"],
        how="left",
        validate="one_to_one",
    ).drop(columns=["date"])
    frame["entry_1001_split"] = frame["entry_1001_raw"] * frame["split_factor"]
    frame = frame[
        frame["signal_complete"]
        & frame["prior20_median_dollar_volume"].ge(100_000_000)
    ].copy()
    sessions = pd.DatetimeIndex(sorted(daily["date"].drop_duplicates()))
    session_number = {pd.Timestamp(date): index for index, date in enumerate(sessions)}
    close_lookup = daily.set_index(["symbol", "date"])["close"]

    confirmations = {}
    loo_rows = []
    bootstrap_report = {}
    for profile_name, profile in PROFILES.items():
        positive = make_candidates(
            frame,
            "positive_after_close_continuation",
            profile["entry_minute"],
            profile["positive_horizon"],
            sessions,
            close_lookup,
        )
        negative = make_candidates(
            frame,
            "negative_high_vol_reclaim",
            profile["entry_minute"],
            profile["negative_horizon"],
            sessions,
            close_lookup,
        )
        candidates = pd.concat([positive, negative], ignore_index=True)
        candidates["mechanism_score"] = (
            candidates["gap_return"].abs() + candidates["first30_return"]
        )
        trades, daily_pnl = simulate_allocated(
            candidates,
            daily,
            sessions,
            session_number,
            profile["cost"],
            profile["cap"],
            profile["rule"],
        )
        source_dir = args.source_artifacts_root / profile["source_dir"]
        source_daily = pd.read_parquet(source_dir / "daily_pnl.parquet")
        source_trades = pd.read_parquet(source_dir / "trade_details.parquet")
        expected_daily = (
            source_daily[
                source_daily["variant_id"].eq(profile["source_variant"])
            ][["date", "net_pnl"]]
            .sort_values("date")
            .reset_index(drop=True)
        )
        expected_trades = source_trades[
            source_trades["variant_id"].eq(profile["source_variant"])
        ]
        reproduced = (
            len(expected_daily) == len(daily_pnl)
            and np.allclose(expected_daily["net_pnl"], daily_pnl["net_pnl"])
            and len(expected_trades) == len(trades)
            and np.isclose(
                expected_trades["trade_pnl"].sum(), trades["trade_pnl"].sum()
            )
        )
        if not reproduced:
            raise RuntimeError(f"Failed exact reproduction: {profile_name}")
        confirmations[profile_name] = {
            "source_variant": profile["source_variant"],
            "candidate_events": int(len(candidates)),
            "allocated_trades": int(trades["position_fraction"].gt(0).sum()),
            "symbols": int(
                trades.loc[trades["position_fraction"].gt(0), "symbol"].nunique()
            ),
            **compact_metrics(daily_pnl),
        }

        allocated = trades[trades["position_fraction"].gt(0)].copy()
        allocated["event_key"] = (
            allocated["symbol"]
            + "|"
            + allocated["event_timestamp"].astype(str)
        )
        for symbol in sorted(allocated["symbol"].unique()):
            loo_trades, loo_daily = simulate_allocated(
                candidates[candidates["symbol"].ne(symbol)],
                daily,
                sessions,
                session_number,
                profile["cost"],
                profile["cap"],
                profile["rule"],
            )
            loo_rows.append(
                {
                    "profile": profile_name,
                    "loo_type": "symbol",
                    "removed": symbol,
                    "allocated_trades": int(
                        loo_trades["position_fraction"].gt(0).sum()
                    ),
                    **compact_metrics(loo_daily),
                }
            )
        for event_key in sorted(allocated["event_key"].unique()):
            symbol, timestamp = event_key.split("|", 1)
            remove = candidates["symbol"].eq(symbol) & candidates[
                "event_timestamp"
            ].astype(str).eq(timestamp)
            loo_trades, loo_daily = simulate_allocated(
                candidates[~remove],
                daily,
                sessions,
                session_number,
                profile["cost"],
                profile["cap"],
                profile["rule"],
            )
            loo_rows.append(
                {
                    "profile": profile_name,
                    "loo_type": "event",
                    "removed": event_key,
                    "allocated_trades": int(
                        loo_trades["position_fraction"].gt(0).sum()
                    ),
                    **compact_metrics(loo_daily),
                }
            )
        full_months = fixed_months(daily_pnl, "2024-07")
        recent_months = fixed_months(daily_pnl, "2025-02")
        rng = np.random.default_rng(SEED)
        bootstrap_report[profile_name] = {
            "full": bootstrap_months(full_months.to_numpy(), rng),
            "recent_15m": bootstrap_months(recent_months.to_numpy(), rng),
            "rolling": rolling_report(full_months),
        }

    loo = pd.DataFrame(loo_rows)
    loo.to_parquet(args.output_dir / "leave_one_out.parquet", index=False)
    loo_summary = {}
    for (profile, loo_type), group in loo.groupby(["profile", "loo_type"]):
        worst_index = group["recent_15m_average_month"].idxmin()
        best_index = group["recent_15m_average_month"].idxmax()
        loo_summary[f"{profile}|{loo_type}"] = {
            "removals": int(len(group)),
            "recent_15m_average_month_min": float(
                group["recent_15m_average_month"].min()
            ),
            "recent_15m_average_month_median": float(
                group["recent_15m_average_month"].median()
            ),
            "recent_15m_average_month_max": float(
                group["recent_15m_average_month"].max()
            ),
            "worst_removal": str(group.loc[worst_index, "removed"]),
            "best_removal": str(group.loc[best_index, "removed"]),
            "maximum_drawdown_max": float(group["maximum_drawdown"].max()),
        }
    validation = {
        "status": "passed",
        "confirmations": confirmations,
        "leave_one_out": loo_summary,
        "bootstrap": bootstrap_report,
    }
    (args.output_dir / "validation.json").write_text(
        json.dumps(validation, indent=2) + "\n", encoding="utf-8"
    )
    frozen_hash = hashlib.sha256(
        json.dumps(
            {
                "profiles": record["frozen_profiles"],
                "validation": record["frozen_validation"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    code_paths = [
        Path(__file__),
        Path(__file__).with_name("run0004.py"),
        Path(__file__).with_name("run0003.py"),
        Path(__file__).with_name("run0001.py"),
        Path(__file__).with_name("cam0007.py"),
    ]
    reconciliation = {
        "status": "passed",
        "profiles_reproduced": len(confirmations),
        "bootstrap_replications_per_profile_and_window": REPLICATIONS,
        "leave_one_out_resimulations": int(len(loo)),
        "frozen_configuration_hash": frozen_hash,
        "input_hashes": {
            "event_readiness": sha256(args.event_readiness),
            "event_minutes": sha256(args.event_minutes),
            "causal_features": sha256(args.causal_features),
            "daily_split": sha256(args.daily_split),
        },
        "executed_code_hashes": {path.name: sha256(path) for path in code_paths},
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(validation, indent=2))


if __name__ == "__main__":
    main()
