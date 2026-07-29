from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from cam0008 import CUTOFF, max_drawdown_and_recovery
from run0001 import sha256, simulate


PROFILES = {
    "failed_negative_fast": {
        "source_variant": (
            "negative_failure_long__w1__l3__ten_close__10bp__cap0.1"
        ),
        "cost": 10,
        "cap": 0.10,
    },
    "failed_negative_slow": {
        "source_variant": (
            "negative_failure_long__w30__l3__ten_close__10bp__cap0.1"
        ),
        "cost": 10,
        "cap": 0.10,
    },
    "combined_midwindow": {
        "source_variant": "all_longs__w15__l0__ten_close__10bp__cap0.1",
        "cost": 10,
        "cap": 0.10,
    },
}
SEED = 8005
REPLICATIONS = 20_000
BLOCK_MONTHS = 3


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
    count = len(values)
    blocks = math.ceil(count / BLOCK_MONTHS)
    starts = rng.integers(0, count, size=(REPLICATIONS, blocks))
    indices = (
        starts[:, :, None] + np.arange(BLOCK_MONTHS)[None, None, :]
    ) % count
    samples = values[indices].reshape(REPLICATIONS, -1)[:, :count]
    averages = samples.mean(axis=1)
    return {
        "months": count,
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
    result: dict[str, dict] = {}
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


def primary_firm(value: str) -> str:
    firms = json.loads(value)
    return str(firms[0]) if firms else "unknown"


def removal_specs(
    allocated: pd.DataFrame,
) -> list[tuple[str, str, pd.Series]]:
    specs: list[tuple[str, str, pd.Series]] = []
    for symbol in sorted(allocated["symbol"].unique()):
        specs.append(
            ("symbol", str(symbol), allocated["symbol"].eq(symbol))
        )

    allocated = allocated.copy()
    allocated["primary_firm"] = allocated["firms"].map(primary_firm)
    firm_pnl = allocated.groupby("primary_firm")["trade_pnl"].sum()
    firms = list(firm_pnl.nlargest(5).index) + list(
        firm_pnl.nsmallest(5).index
    )
    for firm in dict.fromkeys(firms):
        specs.append(
            ("firm", str(firm), allocated["primary_firm"].eq(firm))
        )

    event_pnl = allocated.groupby("event_id")["trade_pnl"].sum()
    events = list(event_pnl.nlargest(10).index) + list(
        event_pnl.nsmallest(10).index
    )
    for event_id in dict.fromkeys(events):
        specs.append(
            ("event", str(event_id), allocated["event_id"].eq(event_id))
        )

    day_pnl = allocated.groupby("entry_session")["trade_pnl"].sum()
    days = list(day_pnl.nlargest(5).index) + list(day_pnl.nsmallest(5).index)
    for date in dict.fromkeys(days):
        specs.append(
            (
                "entry_day",
                str(pd.Timestamp(date).date()),
                allocated["entry_session"].eq(date),
            )
        )
    return specs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record", type=Path, required=True)
    parser.add_argument("--source-trades", type=Path, required=True)
    parser.add_argument("--source-daily", type=Path, required=True)
    parser.add_argument("--source-metrics", type=Path, required=True)
    parser.add_argument("--daily-split", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    record = yaml.safe_load(args.run_record.read_text(encoding="utf-8"))
    if record["status"] != "frozen":
        raise RuntimeError("RUN-0005 record is not frozen")
    frozen_variants = {
        name: value["source_variant"]
        for name, value in record["frozen_profiles"].items()
    }
    if frozen_variants != {
        name: value["source_variant"] for name, value in PROFILES.items()
    }:
        raise RuntimeError("Frozen profiles differ from executable")

    source_trades = pd.read_parquet(args.source_trades)
    source_daily = pd.read_parquet(args.source_daily)
    source_metrics = pd.read_parquet(args.source_metrics)
    daily_prices = pd.read_parquet(args.daily_split)
    source_daily["date"] = pd.to_datetime(source_daily["date"])
    daily_prices["date"] = pd.to_datetime(daily_prices["date"])
    if max(source_daily["date"].max(), daily_prices["date"].max()) > CUTOFF:
        raise RuntimeError("RUN-0005 input crosses sealed boundary")
    sessions = pd.DatetimeIndex(
        sorted(daily_prices["date"].drop_duplicates())
    )

    confirmations: dict[str, dict] = {}
    bootstrap_report: dict[str, dict] = {}
    concentration_report: dict[str, dict] = {}
    loo_rows: list[dict] = []
    for profile_name, profile in PROFILES.items():
        variant = profile["source_variant"]
        candidates = source_trades[
            source_trades["variant_id"].eq(variant)
        ].copy()
        expected_daily = (
            source_daily[source_daily["variant_id"].eq(variant)][
                ["date", "net_pnl"]
            ]
            .sort_values("date")
            .reset_index(drop=True)
        )
        expected_trades = candidates.copy()
        trades, daily_pnl = simulate(
            candidates,
            daily_prices,
            sessions,
            profile["cost"],
            profile["cap"],
        )
        reproduced = (
            len(expected_daily) == len(daily_pnl)
            and np.allclose(expected_daily["net_pnl"], daily_pnl["net_pnl"])
            and len(expected_trades) == len(trades)
            and np.allclose(
                expected_trades["position_fraction"],
                trades["position_fraction"],
            )
            and np.isclose(
                expected_trades["trade_pnl"].sum(),
                trades["trade_pnl"].sum(),
            )
        )
        if not reproduced:
            raise RuntimeError(f"Failed exact reproduction: {profile_name}")
        allocated = trades[trades["position_fraction"].gt(0)].copy()
        confirmations[profile_name] = {
            "source_variant": variant,
            "candidate_events": int(len(candidates)),
            "allocated_trades": int(len(allocated)),
            "symbols": int(allocated["symbol"].nunique()),
            **compact_metrics(daily_pnl),
        }

        allocated["primary_firm"] = allocated["firms"].map(primary_firm)
        positive_total = float(allocated["trade_pnl"].clip(lower=0).sum())
        concentration_report[profile_name] = {
            "top5_event_positive_share": float(
                allocated["trade_pnl"].clip(lower=0).nlargest(5).sum()
                / positive_total
            ),
            "top5_entry_day_positive_share": float(
                allocated.groupby("entry_session")["trade_pnl"]
                .sum()
                .clip(lower=0)
                .nlargest(5)
                .sum()
                / positive_total
            ),
            "top5_symbol_positive_share": float(
                allocated.groupby("symbol")["trade_pnl"]
                .sum()
                .clip(lower=0)
                .nlargest(5)
                .sum()
                / positive_total
            ),
            "top5_firm_positive_share": float(
                allocated.groupby("primary_firm")["trade_pnl"]
                .sum()
                .clip(lower=0)
                .nlargest(5)
                .sum()
                / positive_total
            ),
        }

        candidate_index = candidates.index
        allocated_for_specs = allocated.copy()
        for loo_type, removed, allocated_mask in removal_specs(
            allocated_for_specs
        ):
            if loo_type == "symbol":
                remove = candidates["symbol"].eq(removed)
            elif loo_type == "firm":
                remove = candidates["firms"].map(primary_firm).eq(removed)
            elif loo_type == "event":
                remove = candidates["event_id"].eq(removed)
            elif loo_type == "entry_day":
                remove = candidates["entry_session"].eq(pd.Timestamp(removed))
            else:
                raise KeyError(loo_type)
            if not remove.index.equals(candidate_index):
                raise RuntimeError("Removal mask index mismatch")
            loo_trades, loo_daily = simulate(
                candidates[~remove],
                daily_prices,
                sessions,
                profile["cost"],
                profile["cap"],
            )
            loo_rows.append(
                {
                    "profile": profile_name,
                    "loo_type": loo_type,
                    "removed": removed,
                    "removed_candidate_events": int(remove.sum()),
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
    loo_summary: dict[str, dict] = {}
    for (profile, loo_type), group in loo.groupby(["profile", "loo_type"]):
        worst = group["recent_15m_average_month"].idxmin()
        best = group["recent_15m_average_month"].idxmax()
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
            "worst_removal": str(group.loc[worst, "removed"]),
            "best_removal": str(group.loc[best, "removed"]),
            "maximum_drawdown_max": float(group["maximum_drawdown"].max()),
        }

    adverse_cost: dict[str, dict] = {}
    for profile_name, profile in PROFILES.items():
        adverse_id = profile["source_variant"].replace("__10bp__", "__20bp__")
        row = source_metrics[source_metrics["variant_id"].eq(adverse_id)]
        if len(row) != 1:
            raise RuntimeError(f"Missing adverse-cost match: {profile_name}")
        adverse_cost[profile_name] = {
            "source_variant": adverse_id,
            "recent_15m_average_month": float(
                row.iloc[0]["recent_15m_average_month"]
            ),
            "maximum_drawdown": float(row.iloc[0]["maximum_drawdown"]),
            "positive_months": int(
                row.iloc[0]["recent_15m_positive_months"]
            ),
            "negative_months": int(
                row.iloc[0]["recent_15m_negative_months"]
            ),
        }

    validation = {
        "status": "passed",
        "confirmations": confirmations,
        "concentration": concentration_report,
        "leave_one_out": loo_summary,
        "bootstrap": bootstrap_report,
        "adverse_cost": adverse_cost,
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
    reconciliation = {
        "status": "passed",
        "profiles_reproduced": len(confirmations),
        "bootstrap_replications_per_profile_and_window": REPLICATIONS,
        "leave_one_out_resimulations": int(len(loo)),
        "leave_one_out_counts": {
            f"{profile}|{kind}": int(len(group))
            for (profile, kind), group in loo.groupby(
                ["profile", "loo_type"]
            )
        },
        "frozen_configuration_hash": frozen_hash,
        "input_hashes": {
            "source_trades": sha256(args.source_trades),
            "source_daily": sha256(args.source_daily),
            "source_metrics": sha256(args.source_metrics),
            "daily_split": sha256(args.daily_split),
        },
        "executed_code_hashes": {
            Path(__file__).name: sha256(Path(__file__)),
            "run0001.py": sha256(Path(__file__).with_name("run0001.py")),
            "cam0008.py": sha256(Path(__file__).with_name("cam0008.py")),
        },
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
