from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cam0006 import CUTOFF, max_drawdown_and_recovery


FILTERS = (
    "all",
    "dollar_q50",
    "dollar_q67",
    "dollar_q80",
    "trade_q50",
    "range50",
    "range75",
    "close_vwap",
    "dollar_q50_range50",
)
SLIPPAGE = (0, 2, 5)
MONTHS = pd.period_range("2024-11", "2026-04", freq="M")
WINDOW_STARTS = {
    "18m": pd.Timestamp("2024-11-01"),
    "15m": pd.Timestamp("2025-02-01"),
    "12m": pd.Timestamp("2025-05-01"),
}
BLOCKS = (
    ("block_1", pd.Timestamp("2024-11-01"), pd.Timestamp("2025-04-30")),
    ("block_2", pd.Timestamp("2025-05-01"), pd.Timestamp("2025-10-31")),
    ("block_3", pd.Timestamp("2025-11-01"), pd.Timestamp("2026-04-30")),
)


def load_first_minutes(minutes_path: Path) -> pd.DataFrame:
    con = duckdb.connect()
    try:
        frame = con.execute(
            """
            SELECT symbol, CAST(date AS DATE) AS date,
                   open AS first_open, high AS first_high, low AS first_low,
                   close AS first_close_trade, volume AS first_volume,
                   trade_count AS first_trade_count, vwap AS first_vwap
            FROM read_parquet(?)
            WHERE minute = '09:30'
              AND date BETWEEN DATE '2024-11-01' AND DATE '2026-04-30'
            """,
            [str(minutes_path)],
        ).fetch_df()
    finally:
        con.close()
    frame["date"] = pd.to_datetime(frame["date"])
    if frame.duplicated(["symbol", "date"]).any():
        raise RuntimeError("Duplicate first-minute bar")
    return frame


def build_features(signals_path: Path, minutes_path: Path) -> pd.DataFrame:
    signals = pd.read_parquet(
        signals_path,
        columns=[
            "symbol",
            "date",
            "signal_complete",
            "corporate_action_safe",
            "prior_dollar_volume",
        ],
    )
    signals["date"] = pd.to_datetime(signals["date"])
    signals = signals[
        signals["date"].between("2024-11-01", "2026-04-30")
        & signals["signal_complete"]
        & signals["corporate_action_safe"]
        & signals["prior_dollar_volume"].ge(100_000_000)
    ].copy()
    first = load_first_minutes(minutes_path)
    frame = signals.merge(
        first, on=["symbol", "date"], how="left", validate="one_to_one"
    )
    if frame["first_close_trade"].isna().any():
        raise RuntimeError("Missing completed first minute in causal rank universe")
    frame["first_dollar_participation"] = (
        frame["first_volume"] * frame["first_vwap"] / frame["prior_dollar_volume"]
    )
    frame["dollar_rank"] = frame.groupby("date")[
        "first_dollar_participation"
    ].rank(pct=True, method="average")
    frame["trade_rank"] = frame.groupby("date")["first_trade_count"].rank(
        pct=True, method="average"
    )
    width = frame["first_high"] - frame["first_low"]
    frame["range_close_position"] = np.where(
        width.gt(0),
        (frame["first_close_trade"] - frame["first_low"]) / width,
        0.5,
    )
    frame["close_ge_vwap"] = frame["first_close_trade"].ge(frame["first_vwap"])
    return frame


def eligibility(frame: pd.DataFrame, rule: str) -> pd.Series:
    if rule == "all":
        return pd.Series(True, index=frame.index)
    if rule == "dollar_q50":
        return frame["dollar_rank"].ge(0.50)
    if rule == "dollar_q67":
        return frame["dollar_rank"].ge(0.67)
    if rule == "dollar_q80":
        return frame["dollar_rank"].ge(0.80)
    if rule == "trade_q50":
        return frame["trade_rank"].ge(0.50)
    if rule == "range50":
        return frame["range_close_position"].ge(0.50)
    if rule == "range75":
        return frame["range_close_position"].ge(0.75)
    if rule == "close_vwap":
        return frame["close_ge_vwap"]
    if rule == "dollar_q50_range50":
        return frame["dollar_rank"].ge(0.50) & frame[
            "range_close_position"
        ].ge(0.50)
    raise ValueError(rule)


def evaluate(
    replay: pd.DataFrame, features: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    replay = replay.merge(
        features[
            [
                "symbol",
                "date",
                "first_dollar_participation",
                "dollar_rank",
                "trade_rank",
                "range_close_position",
                "close_ge_vwap",
            ]
        ],
        on=["symbol", "date"],
        how="left",
        validate="one_to_one",
    )
    if replay["dollar_rank"].isna().any():
        raise RuntimeError("Candidate event missing causal first-minute feature")
    variants = []
    month_rows = []
    block_rows = []
    position_rows = []
    for candidate in ("all_state", "vol_high"):
        parent = replay[replay[f"is_{candidate}"] & replay["quote_complete"]].copy()
        for rule in FILTERS:
            selected = parent[eligibility(parent, rule)].copy()
            for slippage in SLIPPAGE:
                name = f"{candidate}_{rule}_nbbo_slip{slippage}"
                frame = selected.copy()
                frame["net_pnl"] = (
                    frame["nbbo_gross_return"] - 2.0 * slippage / 10_000.0
                )
                daily = frame[["date", "net_pnl"]].copy()
                monthly = (
                    daily.assign(month=daily["date"].dt.to_period("M"))
                    .groupby("month")["net_pnl"]
                    .sum()
                    .reindex(MONTHS, fill_value=0.0)
                )
                dd, recovery, unresolved = max_drawdown_and_recovery(daily)
                total = float(daily["net_pnl"].sum())
                symbol_pnl = frame.groupby("symbol")["net_pnl"].sum()
                row = {
                    "variant": name,
                    "candidate": candidate,
                    "eligibility_rule": rule,
                    "additional_slippage_bps_per_side": slippage,
                    "parent_events": int(len(parent)),
                    "eligible_events": int(len(frame)),
                    "eligible_fraction": float(len(frame) / len(parent)),
                    "symbol_count": int(frame["symbol"].nunique()),
                    "full_net_simple_return": total,
                    "standard_max_drawdown": dd,
                    "max_recovery_days": recovery,
                    "recovery_unresolved": unresolved,
                    "top_5_day_profit_share": (
                        float(daily["net_pnl"].nlargest(5).sum() / total)
                        if total > 0
                        else np.nan
                    ),
                    "top_symbol_profit_share": (
                        float(symbol_pnl.max() / total) if total > 0 else np.nan
                    ),
                }
                for label, start in WINDOW_STARTS.items():
                    subset = monthly[monthly.index >= start.to_period("M")]
                    row[f"average_month_{label}"] = float(subset.mean())
                    row[f"negative_months_{label}"] = int((subset < 0).sum())
                    row[f"zero_months_{label}"] = int((subset == 0).sum())
                variants.append(row)
                for month, pnl in monthly.items():
                    month_rows.append(
                        {"variant": name, "month": str(month), "net_pnl": float(pnl)}
                    )
                for block, start, end in BLOCKS:
                    sub = daily[daily["date"].between(start, end)]
                    block_rows.append(
                        {
                            "variant": name,
                            "block": block,
                            "net_pnl": float(sub["net_pnl"].sum()),
                            "event_count": int(len(sub)),
                        }
                    )
                for item in frame.itertuples(index=False):
                    position_rows.append(
                        {
                            "variant": name,
                            "date": item.date,
                            "symbol": item.symbol,
                            "net_pnl": item.net_pnl,
                            "dollar_rank": item.dollar_rank,
                            "trade_rank": item.trade_rank,
                            "range_close_position": item.range_close_position,
                            "close_ge_vwap": item.close_ge_vwap,
                        }
                    )
    return (
        pd.DataFrame(variants),
        pd.DataFrame(month_rows),
        pd.DataFrame(block_rows),
        pd.DataFrame(position_rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals-path", type=Path, required=True)
    parser.add_argument("--minutes-path", type=Path, required=True)
    parser.add_argument("--replay-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    replay = pd.read_parquet(args.replay_path)
    replay["date"] = pd.to_datetime(replay["date"])
    if replay["date"].max() > CUTOFF:
        raise RuntimeError("Sealed replay event loaded")
    features = build_features(args.signals_path, args.minutes_path)
    if features["date"].max() > CUTOFF:
        raise RuntimeError("Sealed feature row loaded")
    variants, monthly, blocks, positions = evaluate(replay, features)
    if len(variants) != 54:
        raise RuntimeError(f"Expected 54 variants, got {len(variants)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    features.to_parquet(args.output_dir / "first_minute_features.parquet", index=False)
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    blocks.to_csv(args.output_dir / "blocks.csv", index=False)
    positions.to_parquet(args.output_dir / "positions.parquet", index=False)
    contract = {
        "command": (
            "python campaigns/CAM-0006/src/run0007.py "
            "--signals-path campaigns/CAM-0006/artifacts/RUN-0002/enriched_signals.parquet "
            "--minutes-path campaigns/CAM-0006/artifacts/readiness/regular_minutes.parquet "
            "--replay-path campaigns/CAM-0006/artifacts/RUN-0006/event_replay.parquet "
            "--output-dir campaigns/CAM-0006/artifacts/RUN-0007"
        ),
        "resolved_defaults": {
            "candidate_event_sets": ["all_state", "vol_high"],
            "eligibility_rules": list(FILTERS),
            "additional_slippage_bps_per_side": list(SLIPPAGE),
        },
        "executed_variant_count": int(len(variants)),
        "causal_feature_universe_rows": int(len(features)),
        "max_loaded_date": str(
            max(replay["date"].max(), features["date"].max()).date()
        ),
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    print(
        variants.sort_values(
            ["average_month_15m", "standard_max_drawdown"],
            ascending=[False, True],
        )
        .head(30)
        .to_string(index=False)
    )
    print(json.dumps(contract, indent=2))


if __name__ == "__main__":
    main()
