from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cam0006 import CUTOFF, max_drawdown_and_recovery


PARENTS = ("tail05_anomaly1", "tail10_anomaly1")
COSTS = (5, 10, 20)
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


def diagnostic_configs() -> list[dict]:
    configs = []
    for allocation in (
        "equal",
        "top1_reclaim",
        "top1_auction_anomaly",
        "top1_composite",
    ):
        configs.append(
            {
                "family": "allocation",
                "allocation": allocation,
                "breadth": "all",
                "market_state": "all",
            }
        )
    for breadth in ("singleton", "clustered"):
        for allocation in ("equal", "top1_reclaim"):
            configs.append(
                {
                    "family": "breadth",
                    "allocation": allocation,
                    "breadth": breadth,
                    "market_state": "all",
                }
            )
    for market_state in ("trend_up", "trend_down", "vol_high", "vol_low"):
        for allocation in ("equal", "top1_reclaim"):
            configs.append(
                {
                    "family": "market_state",
                    "allocation": allocation,
                    "breadth": "all",
                    "market_state": market_state,
                }
            )
    if len(configs) != 16:
        raise RuntimeError("Expected 16 diagnostic configurations")
    return configs


def load_qqq_state(catalog: Path) -> pd.DataFrame:
    con = duckdb.connect(str(catalog), read_only=True)
    try:
        qqq = con.execute(
            """
            SELECT date, close
            FROM bars_1d
            WHERE symbol = 'QQQ'
              AND feed = 'sip'
              AND adjustment = 'raw'
              AND timeframe = '1Day'
              AND date BETWEEN DATE '2024-01-01' AND DATE '2026-04-30'
            ORDER BY date
            """
        ).fetch_df()
    finally:
        con.close()
    qqq["date"] = pd.to_datetime(qqq["date"])
    if qqq["date"].duplicated().any():
        raise RuntimeError("Duplicate QQQ raw daily rows")
    if qqq["date"].max() > CUTOFF:
        raise RuntimeError("Sealed QQQ state row loaded")
    qqq["return"] = qqq["close"].pct_change()
    qqq["prior_close"] = qqq["close"].shift(1)
    qqq["prior_sma20"] = qqq["close"].shift(1).rolling(20, min_periods=20).mean()
    qqq["prior_vol20"] = qqq["return"].shift(1).rolling(20, min_periods=20).std()
    qqq["prior_vol20_median60"] = (
        qqq["prior_vol20"].shift(1).rolling(60, min_periods=40).median()
    )
    qqq["trend_up"] = qqq["prior_close"].ge(qqq["prior_sma20"])
    qqq["trend_down"] = qqq["prior_close"].lt(qqq["prior_sma20"])
    qqq["vol_high"] = qqq["prior_vol20"].ge(qqq["prior_vol20_median60"])
    qqq["vol_low"] = qqq["prior_vol20"].lt(qqq["prior_vol20_median60"])
    return qqq[
        [
            "date",
            "close",
            "prior_close",
            "prior_sma20",
            "prior_vol20",
            "prior_vol20_median60",
            "trend_up",
            "trend_down",
            "vol_high",
            "vol_low",
        ]
    ].copy()


def select_positions(day: pd.DataFrame, allocation: str) -> pd.DataFrame:
    if allocation == "equal":
        selected = day.copy()
        selected["weight"] = 1.0 / len(selected)
        return selected
    if allocation == "top1_reclaim":
        index = day["reclaim_fraction"].idxmax()
    elif allocation == "top1_auction_anomaly":
        index = day["auction_anomaly"].idxmax()
    elif allocation == "top1_composite":
        score = (
            day["reclaim_fraction"].rank(pct=True, method="average")
            + day["auction_anomaly"].rank(pct=True, method="average")
        )
        index = score.idxmax()
    else:
        raise ValueError(allocation)
    selected = day.loc[[index]].copy()
    selected["weight"] = 1.0
    return selected


def evaluate(
    parent_positions: pd.DataFrame, qqq_state: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    variants = []
    monthly_rows = []
    block_rows = []
    position_rows = []
    configs = diagnostic_configs()
    for parent in PARENTS:
        for cost in COSTS:
            parent_name = f"{parent}_0931_none_c{cost}"
            base = parent_positions[
                parent_positions["variant"].eq(parent_name)
            ].copy()
            if base.empty:
                raise RuntimeError(f"Missing parent positions {parent_name}")
            breadth_by_date = base.groupby("date").size().rename("signal_breadth")
            base = base.join(breadth_by_date, on="date")
            base = base.merge(
                qqq_state.drop(columns=["close"]),
                on="date",
                how="left",
                validate="many_to_one",
            )
            if base[["prior_close", "prior_sma20"]].isna().any().any():
                raise RuntimeError(f"Missing causal QQQ state for {parent_name}")
            for config in configs:
                eligible = base.copy()
                breadth = config["breadth"]
                if breadth == "singleton":
                    eligible = eligible[eligible["signal_breadth"].eq(1)]
                elif breadth == "clustered":
                    eligible = eligible[eligible["signal_breadth"].ge(2)]
                market_state = config["market_state"]
                if market_state != "all":
                    eligible = eligible[eligible[market_state]]
                name = (
                    f"{parent}_{config['family']}_{config['allocation']}_"
                    f"{breadth}_{market_state}_c{cost}"
                )
                daily_rows = []
                contributions = []
                for date, day in eligible.groupby("date"):
                    selected = select_positions(day, config["allocation"])
                    selected["portfolio_contribution"] = (
                        selected["event_return"] * selected["weight"]
                    )
                    daily_rows.append(
                        {
                            "date": pd.Timestamp(date),
                            "net_pnl": float(
                                selected["portfolio_contribution"].sum()
                            ),
                            "event_count": int(len(selected)),
                        }
                    )
                    for item in selected.itertuples(index=False):
                        contributions.append(
                            {
                                "variant": name,
                                "date": pd.Timestamp(item.date),
                                "symbol": item.symbol,
                                "event_return": item.event_return,
                                "portfolio_contribution": item.portfolio_contribution,
                                "raw_gap": item.raw_gap,
                                "reclaim_fraction": item.reclaim_fraction,
                                "auction_anomaly": item.auction_anomaly,
                                "signal_breadth": item.signal_breadth,
                            }
                        )
                daily = pd.DataFrame(daily_rows)
                if daily.empty:
                    daily = pd.DataFrame(columns=["date", "net_pnl", "event_count"])
                monthly = (
                    daily.assign(month=daily["date"].dt.to_period("M"))
                    .groupby("month")["net_pnl"]
                    .sum()
                    .reindex(MONTHS, fill_value=0.0)
                )
                dd, recovery, unresolved = max_drawdown_and_recovery(daily)
                total = float(daily["net_pnl"].sum())
                contribution = pd.DataFrame(contributions)
                if contribution.empty:
                    symbol_count = 0
                    top_symbol_share = np.nan
                else:
                    symbol_pnl = contribution.groupby("symbol")[
                        "portfolio_contribution"
                    ].sum()
                    symbol_count = int(len(symbol_pnl))
                    top_symbol_share = (
                        float(symbol_pnl.max() / total) if total > 0 else np.nan
                    )
                row = {
                    "variant": name,
                    "parent": parent,
                    "family": config["family"],
                    "allocation": config["allocation"],
                    "breadth": breadth,
                    "market_state": market_state,
                    "cost_bps_per_side": cost,
                    "full_net_simple_return": total,
                    "standard_max_drawdown": dd,
                    "max_recovery_days": recovery,
                    "recovery_unresolved": unresolved,
                    "parent_valid_events": int(len(base)),
                    "eligible_events": int(len(eligible)),
                    "allocated_events": int(len(contribution)),
                    "active_days": int(len(daily)),
                    "symbol_count_recent": symbol_count,
                    "top_5_day_profit_share": (
                        float(daily["net_pnl"].nlargest(5).sum() / total)
                        if total > 0
                        else np.nan
                    ),
                    "top_10_day_profit_share": (
                        float(daily["net_pnl"].nlargest(10).sum() / total)
                        if total > 0
                        else np.nan
                    ),
                    "top_symbol_profit_share": top_symbol_share,
                }
                for label, start in WINDOW_STARTS.items():
                    subset = monthly[monthly.index >= start.to_period("M")]
                    row[f"average_month_{label}"] = float(subset.mean())
                    row[f"negative_months_{label}"] = int((subset < 0).sum())
                    row[f"zero_months_{label}"] = int((subset == 0).sum())
                variants.append(row)
                for month, pnl in monthly.items():
                    monthly_rows.append(
                        {"variant": name, "month": str(month), "net_pnl": float(pnl)}
                    )
                for block, start, end in BLOCKS:
                    sub = daily[daily["date"].between(start, end)]
                    block_rows.append(
                        {
                            "variant": name,
                            "block": block,
                            "net_pnl": float(sub["net_pnl"].sum()),
                            "active_days": int(len(sub)),
                        }
                    )
                position_rows.extend(contributions)
    return (
        pd.DataFrame(variants),
        pd.DataFrame(monthly_rows),
        pd.DataFrame(block_rows),
        pd.DataFrame(position_rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-positions", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    parent_positions = pd.read_parquet(args.parent_positions)
    parent_positions["date"] = pd.to_datetime(parent_positions["date"])
    if parent_positions["date"].max() > CUTOFF:
        raise RuntimeError("Sealed parent position loaded")
    qqq_state = load_qqq_state(args.catalog)
    variants, monthly, blocks, positions = evaluate(parent_positions, qqq_state)
    if len(variants) != 96:
        raise RuntimeError(f"Expected 96 variants, got {len(variants)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    qqq_state.to_parquet(args.output_dir / "qqq_state.parquet", index=False)
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    blocks.to_csv(args.output_dir / "blocks.csv", index=False)
    positions.to_parquet(args.output_dir / "positions.parquet", index=False)
    contract = {
        "command": (
            "python campaigns/CAM-0006/src/run0004.py "
            "--parent-positions campaigns/CAM-0006/artifacts/RUN-0003/positions.parquet "
            "--catalog D:/AlgoResearch/data/catalog.duckdb "
            "--output-dir campaigns/CAM-0006/artifacts/RUN-0004"
        ),
        "resolved_defaults": {
            "parents": list(PARENTS),
            "diagnostic_configurations_per_parent": len(diagnostic_configs()),
            "cost_bps_per_side": list(COSTS),
        },
        "executed_variant_count": int(len(variants)),
        "max_loaded_date": str(
            max(parent_positions["date"].max(), qqq_state["date"].max()).date()
        ),
        "holdout_rows_loaded": 0,
        "qqq_state_rows": int(len(qqq_state)),
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2), encoding="utf-8"
    )
    print(
        variants.sort_values(
            ["average_month_15m", "standard_max_drawdown"],
            ascending=[False, True],
        )
        .head(40)
        .to_string(index=False)
    )
    print(json.dumps(contract, indent=2))


if __name__ == "__main__":
    main()
