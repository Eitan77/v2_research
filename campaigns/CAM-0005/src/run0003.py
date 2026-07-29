from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0005 import (
    CUTOFF,
    marketable_long_return,
    max_drawdown_and_recovery,
    rolling_prior_quantile,
)


QUANTILES = {"q40": 0.40, "q50": 0.50, "q60": 0.60, "q67": 0.67}
ENTRIES = ["15:56", "15:59"]
EXITS = {
    "next_open": ("09:30", "open"),
    "next_0930_close": ("09:30", "close"),
    "next_0934_close": ("09:34", "close"),
}
PRODUCT_LEGS = [
    "both_reversal",
    "negative_signal_bull",
    "positive_signal_inverse",
    "always_bull_control",
]
UNDERLYING_LEGS = [
    "both_signed_reversal",
    "negative_signal_long",
    "positive_signal_short_diagnostic",
]
WINDOW_STARTS = {
    "18m": pd.Timestamp("2024-11-01"),
    "15m": pd.Timestamp("2025-02-01"),
    "12m": pd.Timestamp("2025-05-01"),
}


def price(
    minutes: pd.DataFrame,
    symbol: str,
    session: pd.Timestamp,
    minute: str,
    field: str,
) -> float | None:
    row = minutes[
        minutes["symbol"].eq(symbol)
        & minutes["session"].eq(session)
        & minutes["minute"].eq(minute)
    ]
    if len(row) != 1:
        return None
    value = float(row.iloc[0][field])
    return value if np.isfinite(value) and value > 0 else None


def prepare_features(parent: pd.DataFrame) -> pd.DataFrame:
    frame = parent[parent["pair"].eq("smh")].sort_values("session").copy()
    for label, quantile in QUANTILES.items():
        frame[f"threshold_{label}"] = rolling_prior_quantile(
            frame["abs_signal"], quantile
        )
    return frame


def build_positions(
    features: pd.DataFrame, minutes: pd.DataFrame
) -> pd.DataFrame:
    rows = []
    for item in features.itertuples():
        if item.session < pd.Timestamp("2024-11-01"):
            continue
        for threshold in QUANTILES:
            boundary = getattr(item, f"threshold_{threshold}")
            if not np.isfinite(boundary) or item.abs_signal < boundary:
                continue
            negative = item.signal_return < 0
            for entry_minute in ENTRIES:
                for exit_name, (exit_minute, exit_field) in EXITS.items():
                    for leg in PRODUCT_LEGS:
                        if leg == "both_reversal":
                            symbol = "SOXL" if negative else "SOXS"
                        elif leg == "negative_signal_bull":
                            if not negative:
                                continue
                            symbol = "SOXL"
                        elif leg == "positive_signal_inverse":
                            if negative:
                                continue
                            symbol = "SOXS"
                        else:
                            symbol = "SOXL"
                        entry = price(
                            minutes,
                            symbol,
                            item.session,
                            entry_minute,
                            "open",
                        )
                        exit_ = price(
                            minutes,
                            symbol,
                            item.next_session,
                            exit_minute,
                            exit_field,
                        )
                        if entry is None or exit_ is None:
                            continue
                        for cost in [2, 5, 10]:
                            rows.append(
                                {
                                    "date": item.session,
                                    "next_session": item.next_session,
                                    "threshold": threshold,
                                    "entry": entry_minute,
                                    "exit": exit_name,
                                    "leg": leg,
                                    "expression": "product_long_only",
                                    "cost_bps_per_side": cost,
                                    "symbol": symbol,
                                    "signal_return": item.signal_return,
                                    "net_pnl": marketable_long_return(
                                        entry, exit_, cost
                                    ),
                                }
                            )
                    underlying_entry = price(
                        minutes,
                        "SMH",
                        item.session,
                        entry_minute,
                        "open",
                    )
                    underlying_exit = price(
                        minutes,
                        "SMH",
                        item.next_session,
                        exit_minute,
                        exit_field,
                    )
                    if underlying_entry is None or underlying_exit is None:
                        continue
                    raw = underlying_exit / underlying_entry - 1.0
                    for leg in UNDERLYING_LEGS:
                        if leg == "both_signed_reversal":
                            value = raw if negative else -raw
                        elif leg == "negative_signal_long":
                            if not negative:
                                continue
                            value = raw
                        else:
                            if negative:
                                continue
                            value = -raw
                        rows.append(
                            {
                                "date": item.session,
                                "next_session": item.next_session,
                                "threshold": threshold,
                                "entry": entry_minute,
                                "exit": exit_name,
                                "leg": leg,
                                "expression": "underlying_diagnostic",
                                "cost_bps_per_side": 0,
                                "symbol": "SMH",
                                "signal_return": item.signal_return,
                                "net_pnl": value,
                            }
                        )
    result = pd.DataFrame(rows)
    result["variant"] = (
        result["threshold"]
        + "_"
        + result["entry"].str.replace(":", "", regex=False)
        + "_"
        + result["exit"]
        + "_"
        + result["leg"]
        + "_c"
        + result["cost_bps_per_side"].astype(str)
    )
    return result


def summarize(
    positions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    month_rows = []
    months = pd.period_range("2024-11", "2026-04", freq="M")
    for variant, frame in positions.groupby("variant"):
        daily = frame.groupby("date", as_index=False)["net_pnl"].sum()
        monthly = (
            daily.assign(month=pd.to_datetime(daily["date"]).dt.to_period("M"))
            .groupby("month")["net_pnl"]
            .sum()
            .reindex(months, fill_value=0.0)
        )
        dd, recovery, unresolved = max_drawdown_and_recovery(daily)
        total = float(daily["net_pnl"].sum())
        dates = sorted(pd.to_datetime(daily["date"]).unique())
        cut = dates[len(dates) // 2]
        early = daily[pd.to_datetime(daily["date"]) < cut]["net_pnl"]
        late = daily[pd.to_datetime(daily["date"]) >= cut]["net_pnl"]
        row = {
            "variant": variant,
            "threshold": frame["threshold"].iloc[0],
            "entry": frame["entry"].iloc[0],
            "exit": frame["exit"].iloc[0],
            "leg": frame["leg"].iloc[0],
            "expression": frame["expression"].iloc[0],
            "cost_bps_per_side": int(frame["cost_bps_per_side"].iloc[0]),
            "full_net_simple_return": total,
            "standard_max_drawdown": dd,
            "max_recovery_days": recovery,
            "recovery_unresolved": unresolved,
            "trade_count": int(len(frame)),
            "win_rate": float(frame["net_pnl"].gt(0).mean()),
            "early_half_net": float(early.sum()),
            "late_half_net": float(late.sum()),
            "top_5_day_profit_share": (
                float(daily["net_pnl"].nlargest(5).sum() / total)
                if total > 0
                else np.nan
            ),
        }
        for label, start in WINDOW_STARTS.items():
            subset = monthly[monthly.index >= start.to_period("M")]
            row[f"average_month_{label}"] = float(subset.mean())
            row[f"negative_months_{label}"] = int((subset < 0).sum())
            row[f"zero_months_{label}"] = int((subset == 0).sum())
        rows.append(row)
        for month, value in monthly.items():
            month_rows.append(
                {"variant": variant, "month": str(month), "net_pnl": value}
            )
    return (
        pd.DataFrame(rows).sort_values(
            ["average_month_15m", "standard_max_drawdown"],
            ascending=[False, True],
        ),
        pd.DataFrame(month_rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--readiness-dir", type=Path, required=True)
    parser.add_argument("--parent-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    minutes = pd.read_parquet(args.readiness_dir / "targeted_minutes.parquet")
    minutes["session"] = pd.to_datetime(minutes["session"])
    features = pd.read_parquet(args.parent_dir / "features.parquet")
    features["session"] = pd.to_datetime(features["session"])
    features["next_session"] = pd.to_datetime(features["next_session"])
    features = prepare_features(features)
    positions = build_positions(features, minutes)
    variants, monthly = summarize(positions)
    if len(variants) != 360:
        raise RuntimeError(f"expected 360 variants, executed {len(variants)}")
    if pd.to_datetime(positions["date"]).max() > CUTOFF:
        raise RuntimeError("cutoff failed")
    positions.to_parquet(args.output_dir / "positions.parquet", index=False)
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    diagnostics = {
        "status": "passed",
        "max_loaded_date": str(minutes["session"].max().date()),
        "holdout_rows_loaded": 0,
        "position_rows": int(len(positions)),
        "variant_count": int(len(variants)),
        "underlying_positive_signal_short_is_diagnostic_only": True,
    }
    (args.output_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "command": (
            "python campaigns/CAM-0005/src/run0003.py "
            "--readiness-dir campaigns/CAM-0005/artifacts/readiness "
            "--parent-dir campaigns/CAM-0005/artifacts/RUN-0002 "
            "--output-dir campaigns/CAM-0005/artifacts/RUN-0003"
        ),
        "resolved_defaults": {
            "thresholds": list(QUANTILES),
            "entries": ENTRIES,
            "exits": list(EXITS),
            "product_legs": PRODUCT_LEGS,
            "product_cost_bps_per_side": [2, 5, 10],
            "underlying_legs": UNDERLYING_LEGS,
            "underlying_cost_bps_per_side": [0],
        },
        "executed_variant_count": int(len(variants)),
        "output_paths": [
            "positions.parquet",
            "variants.csv",
            "monthly.csv",
            "contract.json",
            "diagnostics.json",
        ],
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(variants.head(40).to_string(index=False))


if __name__ == "__main__":
    main()
