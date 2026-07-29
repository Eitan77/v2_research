from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0005 import (
    CUTOFF,
    allocate_pair_pnl,
    direction_product,
    marketable_long_return,
    max_drawdown_and_recovery,
    rolling_prior_quantile,
)


PAIRS = {
    "qqq": {"underlying": "QQQ", "bull": "TQQQ", "inverse": "SQQQ"},
    "smh": {"underlying": "SMH", "bull": "SOXL", "inverse": "SOXS"},
}
THRESHOLDS = {"all": None, "q50": 0.50, "q67": 0.67, "q80": 0.80}
WINDOW_STARTS = {
    "18m": pd.Timestamp("2024-11-01"),
    "15m": pd.Timestamp("2025-02-01"),
    "12m": pd.Timestamp("2025-05-01"),
}


def value_at(
    bars: pd.DataFrame, symbol: str, session: pd.Timestamp, minute: str, field: str
) -> float | None:
    row = bars[
        bars["symbol"].eq(symbol)
        & bars["session"].eq(session)
        & bars["minute"].eq(minute)
    ]
    if len(row) != 1:
        return None
    value = float(row.iloc[0][field])
    return value if np.isfinite(value) and value > 0 else None


def build_features(minutes: pd.DataFrame) -> pd.DataFrame:
    records = []
    sessions = sorted(pd.to_datetime(minutes["session"].unique()))
    next_session = {
        session: sessions[index + 1]
        for index, session in enumerate(sessions[:-1])
    }
    for pair, spec in PAIRS.items():
        underlying = spec["underlying"]
        subset = minutes[minutes["symbol"].eq(underlying)]
        for session, group in subset.groupby("session", sort=True):
            group = group.sort_values("local_ts")
            start = group[group["minute"].eq("15:00")]
            end = group[group["minute"].eq("15:54")]
            if len(start) != 1 or len(end) != 1 or session not in next_session:
                continue
            signal_rows = group[group["minute"].between("15:00", "15:54")]
            if len(signal_rows) < 50:
                continue
            signal = float(end.iloc[0]["close"] / start.iloc[0]["open"] - 1.0)
            log_returns = np.log(signal_rows["close"]).diff().dropna()
            low = float(signal_rows["low"].min())
            high = float(signal_rows["high"].max())
            close = float(end.iloc[0]["close"])
            records.append(
                {
                    "session": pd.Timestamp(session),
                    "next_session": pd.Timestamp(next_session[session]),
                    "pair": pair,
                    "underlying": underlying,
                    "signal_return": signal,
                    "abs_signal": abs(signal),
                    "realized_vol": float(np.sqrt((log_returns**2).sum())),
                    "dollar_volume": float(
                        (signal_rows["vwap"] * signal_rows["volume"]).sum()
                    ),
                    "close_location": (
                        (close - low) / (high - low) if high > low else 0.5
                    ),
                }
            )
    features = pd.DataFrame(records).sort_values(["pair", "session"])
    for label, quantile in THRESHOLDS.items():
        if quantile is None:
            features[f"threshold_{label}"] = 0.0
        else:
            features[f"threshold_{label}"] = features.groupby("pair")[
                "abs_signal"
            ].transform(lambda x, q=quantile: rolling_prior_quantile(x, q))
    return features


def build_positions(features: pd.DataFrame, minutes: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for item in features.itertuples():
        if item.session < pd.Timestamp("2024-11-01"):
            continue
        spec = PAIRS[item.pair]
        for threshold in THRESHOLDS:
            boundary = getattr(item, f"threshold_{threshold}")
            if not np.isfinite(boundary) or item.abs_signal < boundary:
                continue
            for mapping in ["continuation", "reversal"]:
                product = direction_product(
                    item.signal_return, mapping, spec["bull"], spec["inverse"]
                )
                product_entry = value_at(
                    minutes, product, item.session, "15:56", "open"
                )
                product_exit = value_at(
                    minutes, product, item.next_session, "09:30", "open"
                )
                underlying_entry = value_at(
                    minutes, item.underlying, item.session, "15:56", "open"
                )
                underlying_exit = value_at(
                    minutes,
                    item.underlying,
                    item.next_session,
                    "09:30",
                    "open",
                )
                if product_entry is not None and product_exit is not None:
                    for cost in [2, 5, 10]:
                        rows.append(
                            {
                                "session": item.session,
                                "next_session": item.next_session,
                                "pair": item.pair,
                                "mapping": mapping,
                                "threshold": threshold,
                                "expression": "product_long_only",
                                "cost_bps_per_side": cost,
                                "symbol": product,
                                "signal_return": item.signal_return,
                                "net_return": marketable_long_return(
                                    product_entry, product_exit, cost
                                ),
                            }
                        )
                if underlying_entry is not None and underlying_exit is not None:
                    raw = underlying_exit / underlying_entry - 1.0
                    signed = np.sign(item.signal_return) * raw
                    if mapping == "reversal":
                        signed *= -1.0
                    rows.append(
                        {
                            "session": item.session,
                            "next_session": item.next_session,
                            "pair": item.pair,
                            "mapping": mapping,
                            "threshold": threshold,
                            "expression": "underlying_signed_diagnostic",
                            "cost_bps_per_side": 0,
                            "symbol": item.underlying,
                            "signal_return": item.signal_return,
                            "net_return": signed,
                        }
                    )
    return pd.DataFrame(rows)


def make_portfolios(positions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    configs = positions[
        ["mapping", "threshold", "expression", "cost_bps_per_side"]
    ].drop_duplicates()
    for config in configs.itertuples(index=False):
        subset = positions[
            positions["mapping"].eq(config.mapping)
            & positions["threshold"].eq(config.threshold)
            & positions["expression"].eq(config.expression)
            & positions["cost_bps_per_side"].eq(config.cost_bps_per_side)
        ]
        for portfolio in ["qqq", "smh", "combined_available"]:
            selected = (
                subset
                if portfolio == "combined_available"
                else subset[subset["pair"].eq(portfolio)]
            )
            for session, group in selected.groupby("session", sort=True):
                rows.append(
                    {
                        "date": pd.Timestamp(session),
                        "variant": (
                            f"{config.mapping}_{config.threshold}_"
                            f"{config.expression}_{portfolio}_"
                            f"c{config.cost_bps_per_side}"
                        ),
                        "mapping": config.mapping,
                        "threshold": config.threshold,
                        "expression": config.expression,
                        "portfolio": portfolio,
                        "cost_bps_per_side": config.cost_bps_per_side,
                        "net_pnl": allocate_pair_pnl(
                            group["net_return"].tolist()
                        ),
                        "pair_count": int(group["pair"].nunique()),
                    }
                )
    return pd.DataFrame(rows)


def summarize(
    decisions: pd.DataFrame, positions: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    metrics = []
    months = pd.period_range("2024-11", "2026-04", freq="M")
    month_rows = []
    concentration_rows = []
    for variant, frame in decisions.groupby("variant"):
        daily = frame.groupby("date", as_index=False)["net_pnl"].sum()
        monthly = (
            daily.assign(month=daily["date"].dt.to_period("M"))
            .groupby("month")["net_pnl"]
            .sum()
            .reindex(months, fill_value=0.0)
        )
        dd, recovery, unresolved = max_drawdown_and_recovery(daily)
        total = float(daily["net_pnl"].sum())
        row = {
            "variant": variant,
            "mapping": frame["mapping"].iloc[0],
            "threshold": frame["threshold"].iloc[0],
            "expression": frame["expression"].iloc[0],
            "portfolio": frame["portfolio"].iloc[0],
            "cost_bps_per_side": int(frame["cost_bps_per_side"].iloc[0]),
            "full_net_simple_return": total,
            "standard_max_drawdown": dd,
            "max_recovery_days": recovery,
            "recovery_unresolved": unresolved,
            "decision_count": int(len(frame)),
            "trading_days": int(daily["date"].nunique()),
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
        metrics.append(row)
        for month, value in monthly.items():
            month_rows.append(
                {"variant": variant, "month": str(month), "net_pnl": value}
            )
        concentration_rows.append(
            {
                "variant": variant,
                "top_day": float(daily["net_pnl"].max()),
                "bottom_day": float(daily["net_pnl"].min()),
                "top_5_day_profit_share": row["top_5_day_profit_share"],
            }
        )
    return (
        pd.DataFrame(metrics).sort_values(
            ["average_month_15m", "standard_max_drawdown"],
            ascending=[False, True],
        ),
        pd.DataFrame(month_rows),
        pd.DataFrame(concentration_rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--readiness-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    minutes = pd.read_parquet(args.readiness_dir / "targeted_minutes.parquet")
    minutes["session"] = pd.to_datetime(minutes["session"])
    features = build_features(minutes)
    positions = build_positions(features, minutes)
    decisions = make_portfolios(positions)
    variants, monthly, concentration = summarize(decisions, positions)
    if len(variants) != 96:
        raise RuntimeError(f"expected 96 variants, executed {len(variants)}")
    if pd.to_datetime(decisions["date"]).max() > CUTOFF:
        raise RuntimeError("cutoff failed")
    features.to_parquet(args.output_dir / "features.parquet", index=False)
    positions.to_parquet(args.output_dir / "positions.parquet", index=False)
    decisions.to_parquet(
        args.output_dir / "portfolio_decisions.parquet", index=False
    )
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    concentration.to_csv(args.output_dir / "concentration.csv", index=False)
    diagnostics = {
        "status": "passed",
        "max_loaded_date": str(minutes["session"].max().date()),
        "holdout_rows_loaded": 0,
        "feature_rows": int(len(features)),
        "position_rows": int(len(positions)),
        "decision_rows": int(len(decisions)),
        "variant_count": int(len(variants)),
        "feature_pair_session_rows": features.groupby("pair").size().to_dict(),
        "position_pair_rows": positions.groupby("pair").size().to_dict(),
        "underlying_negative_signed_exposure_is_diagnostic_only": True,
    }
    (args.output_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8"
    )
    contract = {
        "command": (
            "python campaigns/CAM-0005/src/run0001.py "
            "--readiness-dir campaigns/CAM-0005/artifacts/readiness "
            f"--output-dir {args.output_dir.as_posix()}"
        ),
        "resolved_defaults": {
            "signal_window": "15:00_open_to_15:54_close",
            "decision_time": "15:55_ET",
            "entry": "15:56_open",
            "exit": "next_session_09:30_open",
            "mappings": ["continuation", "reversal"],
            "thresholds": list(THRESHOLDS),
            "portfolios": ["qqq", "smh", "combined_available"],
            "expressions": [
                "product_long_only",
                "underlying_signed_diagnostic",
            ],
            "product_cost_bps_per_side": [2, 5, 10],
            "underlying_cost_bps_per_side": [0],
        },
        "executed_variant_count": int(len(variants)),
        "output_paths": [
            "features.parquet",
            "positions.parquet",
            "portfolio_decisions.parquet",
            "variants.csv",
            "monthly.csv",
            "concentration.csv",
            "contract.json",
            "diagnostics.json",
        ],
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(variants.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
