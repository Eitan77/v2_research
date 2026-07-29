from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from run0001 import CUTOFF, metrics, sha256
from run0005 import allocate_pairs


CANDIDATE_ID = "both__unhedged__p3__cap0.2000__5bp"
RECENT_START = pd.Timestamp("2025-02-01")


def construct(
    executable: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    excluded_leader: str | None = None,
    excluded_peer: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    selected = executable.copy()
    if excluded_leader is not None:
        selected = selected[selected["leader_symbol"].ne(excluded_leader)]
    if excluded_peer is not None:
        selected = selected[selected["symbol"].ne(excluded_peer)]
    selected = (
        selected.sort_values(
            ["event_id", "prior20_median_dollar_volume", "symbol"],
            ascending=[True, False, True],
        )
        .groupby("event_id", sort=False)
        .head(3)
        .copy()
    )
    selected["hedge_ratio"] = 0.0
    allocated = allocate_pairs(selected, 0.20)
    allocated = allocated[allocated["pair_gross"].gt(0)].copy()
    is_long = allocated["trade_direction"].eq("long")
    allocated["unit_net_return"] = np.where(
        is_long,
        allocated["peer_long_gross"],
        allocated["peer_short_gross"],
    ) - 2 * 5 / 10_000
    allocated["position_fraction"] = allocated["pair_gross"]
    allocated["trade_pnl"] = (
        allocated["unit_net_return"] * allocated["pair_gross"]
    )
    allocated["stopped"] = np.where(
        is_long, False, allocated["peer_short_stopped"]
    ).astype(bool)
    allocated["variant_id"] = CANDIDATE_ID
    values = allocated.groupby("date")["trade_pnl"].sum()
    daily = pd.DataFrame({"date": sessions})
    daily["net_pnl"] = daily["date"].map(values).fillna(0.0)
    daily["variant_id"] = CANDIDATE_ID
    row = metrics(
        CANDIDATE_ID, "smh", "both", "close", 5, allocated, daily
    )
    row["input_candidates"] = int(len(selected))
    return allocated, daily, row


def moving_block_bootstrap(
    values: np.ndarray,
    replicates: int,
    block: int,
    seed: int,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = len(values)
    blocks_needed = int(np.ceil(n / block))
    output = np.empty(replicates, dtype=float)
    offsets = np.arange(block)
    for start in range(0, replicates, 2000):
        count = min(2000, replicates - start)
        starts = rng.integers(0, n, size=(count, blocks_needed))
        indices = (starts[:, :, None] + offsets) % n
        samples = values[indices.reshape(count, -1)[:, :n]]
        output[start:start + count] = samples.sum(axis=1) / 15.0
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--minute", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    record = yaml.safe_load(args.run_record.read_text(encoding="utf-8"))
    if record["status"] != "frozen":
        raise RuntimeError("RUN-0006 record is not frozen")
    frozen = record["frozen_configuration"]["bootstrap"]
    if (
        frozen["replicates"] != 20000
        or frozen["moving_block_sessions"] != 5
        or frozen["seed"] != 9006
    ):
        raise RuntimeError("RUN-0006 bootstrap configuration mismatch")
    executable = pd.read_parquet(args.executable)
    executable["date"] = pd.to_datetime(executable["date"])
    minutes = pd.read_parquet(args.minute, columns=["date"])
    minutes["date"] = pd.to_datetime(minutes["date"])
    maximum_date = max(executable["date"].max(), minutes["date"].max())
    if maximum_date > CUTOFF:
        raise RuntimeError("RUN-0006 adversity crosses sealed boundary")
    sessions = pd.DatetimeIndex(sorted(minutes["date"].unique()))

    baseline_trades, baseline_daily, baseline = construct(
        executable, sessions
    )
    loo_rows = []
    for dimension, values in [
        ("leader", sorted(executable["leader_symbol"].unique())),
        ("peer", sorted(executable["symbol"].unique())),
    ]:
        for value in values:
            _, _, row = construct(
                executable,
                sessions,
                excluded_leader=value if dimension == "leader" else None,
                excluded_peer=value if dimension == "peer" else None,
            )
            loo_rows.append(
                {
                    "dimension": dimension,
                    "excluded": value,
                    "recent_15m_average_month": row[
                        "recent_15m_average_month"
                    ],
                    "recent_12m_average_month": row[
                        "recent_12m_average_month"
                    ],
                    "full_average_month": row["full_average_month"],
                    "maximum_drawdown": row["maximum_drawdown"],
                    "recovery_days": row["recovery_days"],
                    "recent_15m_positive_months": row[
                        "recent_15m_positive_months"
                    ],
                    "recent_15m_negative_months": row[
                        "recent_15m_negative_months"
                    ],
                    "allocated_trades": row["allocated_trades"],
                    "delta_recent_15m": (
                        row["recent_15m_average_month"]
                        - baseline["recent_15m_average_month"]
                    ),
                }
            )
    loo = pd.DataFrame(loo_rows)
    loo.to_parquet(args.output_dir / "capacity_aware_leave_one_out.parquet", index=False)

    recent_daily = baseline_daily[
        baseline_daily["date"].ge(RECENT_START)
    ]["net_pnl"].to_numpy()
    boot = moving_block_bootstrap(recent_daily, 20000, 5, 9006)
    bootstrap = {
        "replicates": 20000,
        "block_sessions": 5,
        "seed": 9006,
        "sessions": int(len(recent_daily)),
        "q01_average_month": float(np.quantile(boot, 0.01)),
        "q05_average_month": float(np.quantile(boot, 0.05)),
        "median_average_month": float(np.quantile(boot, 0.50)),
        "q95_average_month": float(np.quantile(boot, 0.95)),
        "q99_average_month": float(np.quantile(boot, 0.99)),
        "probability_nonpositive": float(np.mean(boot <= 0)),
        "probability_at_least_13pct_month": float(np.mean(boot >= 0.13)),
    }
    (args.output_dir / "bootstrap.json").write_text(
        json.dumps(bootstrap, indent=2), encoding="utf-8"
    )
    baseline_trades.to_parquet(
        args.output_dir / "adversity_candidate_trades.parquet", index=False
    )
    baseline_daily.to_parquet(
        args.output_dir / "adversity_candidate_daily.parquet", index=False
    )
    monthly = (
        baseline_daily.assign(
            month=baseline_daily["date"].dt.to_period("M").astype(str)
        )
        .groupby("month", as_index=False)["net_pnl"].sum()
    )
    monthly.to_parquet(args.output_dir / "adversity_candidate_monthly.parquet", index=False)

    leader_loo = loo[loo["dimension"].eq("leader")]
    peer_loo = loo[loo["dimension"].eq("peer")]
    report = {
        "status": "passed",
        "candidate_id": CANDIDATE_ID,
        "baseline": baseline,
        "leave_one_out": {
            "leaders_tested": int(len(leader_loo)),
            "leader_min_recent_15m_average_month": float(
                leader_loo["recent_15m_average_month"].min()
            ),
            "leader_max_recent_15m_average_month": float(
                leader_loo["recent_15m_average_month"].max()
            ),
            "leader_all_positive_recent_15m": bool(
                leader_loo["recent_15m_average_month"].gt(0).all()
            ),
            "peers_tested": int(len(peer_loo)),
            "peer_min_recent_15m_average_month": float(
                peer_loo["recent_15m_average_month"].min()
            ),
            "peer_max_recent_15m_average_month": float(
                peer_loo["recent_15m_average_month"].max()
            ),
            "peer_all_positive_recent_15m": bool(
                peer_loo["recent_15m_average_month"].gt(0).all()
            ),
        },
        "bootstrap": bootstrap,
        "reconciliation": {
            "maximum_loaded_date": str(maximum_date.date()),
            "holdout_rows_loaded": 0,
            "executable_hash": sha256(args.executable),
            "minute_hash": sha256(args.minute),
            "code_hash": sha256(Path(__file__)),
            "frozen_configuration_hash": hashlib.sha256(
                yaml.safe_dump(
                    record["frozen_configuration"], sort_keys=True
                ).encode()
            ).hexdigest(),
        },
    }
    (args.output_dir / "adversity_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
