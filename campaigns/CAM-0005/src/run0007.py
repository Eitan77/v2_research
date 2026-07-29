from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0005 import CUTOFF, max_drawdown_and_recovery


THRESHOLDS = ("q50", "q60")
SLIPPAGES = (2, 5, 10)
BLOCKS = (
    ("block_1", pd.Timestamp("2024-11-01"), pd.Timestamp("2025-04-30")),
    ("block_2", pd.Timestamp("2025-05-01"), pd.Timestamp("2025-10-31")),
    ("block_3", pd.Timestamp("2025-11-01"), pd.Timestamp("2026-04-30")),
)
SEED = 20260729
BOOTSTRAP_REPLICATES = 20_000
BOOTSTRAP_BLOCKS = (20, 60)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def complete_daily(
    frame: pd.DataFrame, sessions: pd.DatetimeIndex
) -> pd.DataFrame:
    daily = (
        frame.groupby("date", as_index=False)["net_pnl"]
        .sum()
        .assign(date=lambda x: pd.to_datetime(x["date"]))
        .set_index("date")
        .reindex(sessions, fill_value=0.0)
        .rename_axis("date")
        .reset_index()
    )
    return daily


def moving_block_sample(
    values: np.ndarray, block_length: int, rng: np.random.Generator
) -> np.ndarray:
    n = len(values)
    starts = rng.integers(0, n, size=int(np.ceil(n / block_length)))
    offsets = np.arange(block_length)
    return np.concatenate(
        [values[(start + offsets) % n] for start in starts]
    )[:n]


def bootstrap_summary(
    daily: pd.DataFrame, block_length: int, seed: int
) -> dict[str, float | int]:
    values = daily["net_pnl"].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    total = np.empty(BOOTSTRAP_REPLICATES)
    drawdown = np.empty(BOOTSTRAP_REPLICATES)
    for i in range(BOOTSTRAP_REPLICATES):
        sample = moving_block_sample(values, block_length, rng)
        total[i] = sample.sum()
        equity = 1.0 + np.cumsum(sample)
        peaks = np.maximum.accumulate(np.r_[1.0, equity])[:-1]
        drawdown[i] = np.max((peaks - equity) / peaks)
    return {
        "block_length_sessions": block_length,
        "replicates": BOOTSTRAP_REPLICATES,
        "total_return_p05": float(np.quantile(total, 0.05)),
        "total_return_p25": float(np.quantile(total, 0.25)),
        "total_return_median": float(np.quantile(total, 0.50)),
        "total_return_p75": float(np.quantile(total, 0.75)),
        "total_return_p95": float(np.quantile(total, 0.95)),
        "probability_total_return_positive": float(np.mean(total > 0)),
        "max_drawdown_p50": float(np.quantile(drawdown, 0.50)),
        "max_drawdown_p95": float(np.quantile(drawdown, 0.95)),
    }


def run_audit(
    replay: pd.DataFrame, sessions: pd.DatetimeIndex
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    summary_rows: list[dict] = []
    decomposition_rows: list[dict] = []
    tail_rows: list[dict] = []
    monthly_rows: list[dict] = []
    bootstrap: dict[str, dict] = {}
    months = pd.period_range("2024-11", "2026-04", freq="M")

    for threshold in THRESHOLDS:
        selected = replay[replay[f"is_{threshold}"] & replay["quote_complete"]].copy()
        for slippage in SLIPPAGES:
            variant = f"{threshold}_nbbo_slip{slippage}"
            frame = selected.copy()
            frame["date"] = pd.to_datetime(frame["date"])
            frame["net_pnl"] = frame["nbbo_gross_return"] - 2 * slippage / 10_000
            daily = complete_daily(frame, sessions)
            monthly = (
                daily.assign(month=daily["date"].dt.to_period("M"))
                .groupby("month")["net_pnl"]
                .sum()
                .reindex(months, fill_value=0.0)
            )
            dd, recovery, unresolved = max_drawdown_and_recovery(daily)
            total = float(daily["net_pnl"].sum())
            summary_rows.append(
                {
                    "variant": variant,
                    "threshold": threshold,
                    "additional_slippage_bps_per_side": slippage,
                    "total_net_simple_return": total,
                    "average_month_18m": float(monthly.mean()),
                    "median_month_18m": float(monthly.median()),
                    "negative_months_18m": int((monthly < 0).sum()),
                    "positive_months_18m": int((monthly > 0).sum()),
                    "zero_months_18m": int((monthly == 0).sum()),
                    "standard_max_drawdown": dd,
                    "max_recovery_days": recovery,
                    "recovery_unresolved": unresolved,
                    "trade_count": int(len(frame)),
                    "win_rate": float((frame["net_pnl"] > 0).mean()),
                    "mean_event_return": float(frame["net_pnl"].mean()),
                    "median_event_return": float(frame["net_pnl"].median()),
                    "worst_event_return": float(frame["net_pnl"].min()),
                }
            )
            for month, pnl in monthly.items():
                monthly_rows.append(
                    {"variant": variant, "month": str(month), "net_pnl": float(pnl)}
                )
            for name, start, end in BLOCKS:
                sub = frame[frame["date"].between(start, end)]
                decomposition_rows.append(
                    {
                        "variant": variant,
                        "dimension": "chronological_block",
                        "bucket": name,
                        "start": str(start.date()),
                        "end": str(end.date()),
                        "trades": int(len(sub)),
                        "net_pnl": float(sub["net_pnl"].sum()),
                        "mean_event": float(sub["net_pnl"].mean()) if len(sub) else np.nan,
                    }
                )
            for symbol, sub in frame.groupby("symbol"):
                decomposition_rows.append(
                    {
                        "variant": variant,
                        "dimension": "symbol",
                        "bucket": symbol,
                        "start": "",
                        "end": "",
                        "trades": int(len(sub)),
                        "net_pnl": float(sub["net_pnl"].sum()),
                        "mean_event": float(sub["net_pnl"].mean()),
                    }
                )
            for year, sub in frame.groupby(frame["date"].dt.year):
                decomposition_rows.append(
                    {
                        "variant": variant,
                        "dimension": "calendar_year",
                        "bucket": str(year),
                        "start": "",
                        "end": "",
                        "trades": int(len(sub)),
                        "net_pnl": float(sub["net_pnl"].sum()),
                        "mean_event": float(sub["net_pnl"].mean()),
                    }
                )
            ranked = frame.sort_values("net_pnl", ascending=False)
            for k in (1, 3, 5, 10):
                retained = ranked.iloc[k:]
                tail_rows.append(
                    {
                        "variant": variant,
                        "test": "remove_top_profit_days",
                        "removed_count": k,
                        "retained_net_pnl": float(retained["net_pnl"].sum()),
                        "retained_fraction_of_total": float(
                            retained["net_pnl"].sum() / total
                        ),
                    }
                )
            leave_month = [
                float(total - pnl) for pnl in monthly.to_numpy(dtype=float)
            ]
            tail_rows.append(
                {
                    "variant": variant,
                    "test": "leave_one_month_out_min",
                    "removed_count": 1,
                    "retained_net_pnl": min(leave_month),
                    "retained_fraction_of_total": min(leave_month) / total,
                }
            )
            bootstrap[variant] = {}
            for block_length in BOOTSTRAP_BLOCKS:
                bootstrap[variant][str(block_length)] = bootstrap_summary(
                    daily, block_length, SEED + slippage + block_length + (50 if threshold == "q50" else 60)
                )
    return (
        pd.DataFrame(summary_rows),
        pd.DataFrame(decomposition_rows),
        pd.DataFrame(tail_rows),
        pd.DataFrame(monthly_rows),
        bootstrap,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-path", type=Path, required=True)
    parser.add_argument("--calendar-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    replay = pd.read_parquet(args.replay_path)
    calendar = pd.read_parquet(args.calendar_path)
    replay["date"] = pd.to_datetime(replay["date"])
    replay["next_session"] = pd.to_datetime(replay["next_session"])
    if replay["next_session"].max() > CUTOFF or calendar["date"].max() > CUTOFF:
        raise RuntimeError("Sealed holdout row loaded")
    sessions = pd.DatetimeIndex(
        sorted(
            pd.to_datetime(
                calendar.loc[
                    calendar["symbol"].eq("SMH")
                    & pd.to_datetime(calendar["date"]).between(
                        "2024-11-01", "2026-04-30"
                    ),
                    "date",
                ]
            ).unique()
        )
    )
    if sessions.min() != pd.Timestamp("2024-11-01") and sessions.min() != pd.Timestamp("2024-11-04"):
        raise RuntimeError(f"Unexpected first session {sessions.min()}")
    if sessions.max() != pd.Timestamp("2026-04-30"):
        raise RuntimeError(f"Unexpected final session {sessions.max()}")

    summary, decomposition, tails, monthly, bootstrap = run_audit(replay, sessions)
    if len(summary) != 6:
        raise RuntimeError(f"Expected 6 variants, got {len(summary)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.output_dir / "summary.csv", index=False)
    decomposition.to_csv(args.output_dir / "decomposition.csv", index=False)
    tails.to_csv(args.output_dir / "tail_tests.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    (args.output_dir / "bootstrap.json").write_text(
        json.dumps(bootstrap, indent=2), encoding="utf-8"
    )
    contract = {
        "command": (
            "python campaigns/CAM-0005/src/run0007.py "
            "--replay-path campaigns/CAM-0005/artifacts/RUN-0006/event_replay.parquet "
            "--calendar-path campaigns/CAM-0005/artifacts/readiness/split_daily.parquet "
            "--output-dir campaigns/CAM-0005/artifacts/RUN-0007"
        ),
        "resolved_defaults": {
            "thresholds": list(THRESHOLDS),
            "additional_slippage_bps_per_side": list(SLIPPAGES),
            "bootstrap_seed": SEED,
            "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "bootstrap_block_lengths": list(BOOTSTRAP_BLOCKS),
            "bootstrap_method": "circular_moving_block",
            "include_zero_return_sessions": True,
        },
        "executed_variant_count": int(len(summary)),
        "input_hashes": {
            "replay": sha256(args.replay_path),
            "calendar": sha256(args.calendar_path),
        },
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2), encoding="utf-8"
    )
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
