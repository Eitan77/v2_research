from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0006 import CUTOFF, marketable_long_return, max_drawdown_and_recovery


GAP_TAILS = (0.02, 0.05, 0.10, 0.15)
RECLAIMS = (0.0, 0.10, 0.25, 0.50)
AUCTION_STATES = (
    "all",
    "participation_q50",
    "participation_q67",
    "participation_q80",
    "anomaly_ge1",
    "anomaly_ge1_5",
    "anomaly_ge2",
)
HORIZONS = ("1200", "final")
COSTS = (5, 10, 20)
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


def enrich_states(
    signals: pd.DataFrame, event_readiness: pd.DataFrame
) -> pd.DataFrame:
    history = event_readiness.sort_values(["symbol", "date"]).copy()
    history["auction_dollar_median60"] = history.groupby("symbol")[
        "auction_dollar_value"
    ].transform(
        lambda series: series.shift(1).rolling(60, min_periods=40).median()
    )
    history["auction_anomaly"] = (
        history["auction_dollar_value"] / history["auction_dollar_median60"]
    )
    state = history[["symbol", "date", "auction_anomaly"]]
    frame = signals.merge(
        state, on=["symbol", "date"], how="left", validate="one_to_one"
    )
    frame["auction_participation"] = (
        frame["auction_dollar_value"] / frame["prior_dollar_volume"]
    )
    frame["participation_rank"] = frame.groupby("date")[
        "auction_participation"
    ].rank(pct=True, method="average")
    frame["reclaim_fraction"] = (
        frame["first_minute_return"] / (-frame["raw_gap"])
    )
    return frame


def auction_mask(frame: pd.DataFrame, state: str) -> pd.Series:
    if state == "all":
        return pd.Series(True, index=frame.index)
    if state == "participation_q50":
        return frame["participation_rank"].ge(0.50)
    if state == "participation_q67":
        return frame["participation_rank"].ge(0.67)
    if state == "participation_q80":
        return frame["participation_rank"].ge(0.80)
    if state == "anomaly_ge1":
        return frame["auction_anomaly"].ge(1.0)
    if state == "anomaly_ge1_5":
        return frame["auction_anomaly"].ge(1.5)
    if state == "anomaly_ge2":
        return frame["auction_anomaly"].ge(2.0)
    raise ValueError(state)


def evaluate(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows: list[dict] = []
    monthly_rows: list[dict] = []
    block_rows: list[dict] = []
    position_rows: list[dict] = []
    months = pd.period_range("2024-11", "2026-04", freq="M")
    recent_start = pd.Timestamp("2024-11-01")

    # Reconstruct the mechanism independently of RUN-0001's fixed 10% tail.
    # The parent signal flags are diagnostics from that run, not eligibility
    # inputs for this run's wider/narrower causal gap-tail neighborhood.
    base = frame[
        frame["raw_gap"].le(-0.005)
        & frame["first_minute_return"].gt(0)
        & frame["entry_open"].gt(0)
    ].copy()
    for tail in GAP_TAILS:
        tail_mask = base["gap_rank"].le(tail)
        for reclaim in RECLAIMS:
            reclaim_mask = base["reclaim_fraction"].ge(reclaim)
            for state in AUCTION_STATES:
                selected_base = base[
                    tail_mask & reclaim_mask & auction_mask(base, state)
                ].copy()
                for horizon in HORIZONS:
                    exit_column = f"exit_{horizon}"
                    for cost in COSTS:
                        name = (
                            f"tail{int(tail*100):02d}_"
                            f"reclaim{int(reclaim*100):02d}_{state}_"
                            f"{horizon}_c{cost}"
                        )
                        daily_rows = []
                        valid_position_rows = []
                        for date, group in selected_base.groupby("date"):
                            date = pd.Timestamp(date)
                            valid = group[exit_column].gt(0).all()
                            if not valid:
                                daily_rows.append(
                                    {
                                        "date": date,
                                        "net_pnl": 0.0,
                                        "valid_signal_day": False,
                                        "event_count": 0,
                                    }
                                )
                                continue
                            returns = [
                                marketable_long_return(
                                    float(item.entry_open),
                                    float(getattr(item, exit_column)),
                                    cost,
                                )
                                for item in group.itertuples()
                            ]
                            count = len(returns)
                            pnl = float(np.mean(returns))
                            daily_rows.append(
                                {
                                    "date": date,
                                    "net_pnl": pnl,
                                    "valid_signal_day": True,
                                    "event_count": count,
                                }
                            )
                            for item, event_return in zip(
                                group.itertuples(), returns, strict=True
                            ):
                                valid_position_rows.append(
                                    {
                                        "variant": name,
                                        "date": date,
                                        "symbol": item.symbol,
                                        "event_return": event_return,
                                        "portfolio_contribution": event_return / count,
                                        "raw_gap": item.raw_gap,
                                        "reclaim_fraction": item.reclaim_fraction,
                                        "auction_participation": item.auction_participation,
                                        "auction_anomaly": item.auction_anomaly,
                                    }
                                )
                        daily = pd.DataFrame(daily_rows)
                        if daily.empty:
                            daily = pd.DataFrame(
                                columns=[
                                    "date",
                                    "net_pnl",
                                    "valid_signal_day",
                                    "event_count",
                                ]
                            )
                        recent = daily[daily["date"].ge(recent_start)].copy()
                        monthly = (
                            recent.assign(month=recent["date"].dt.to_period("M"))
                            .groupby("month")["net_pnl"]
                            .sum()
                            .reindex(months, fill_value=0.0)
                        )
                        dd, recovery, unresolved = max_drawdown_and_recovery(recent)
                        total = float(recent["net_pnl"].sum())
                        active = recent[recent["net_pnl"].ne(0)]
                        recent_positions = [
                            item
                            for item in valid_position_rows
                            if item["date"] >= recent_start
                        ]
                        contribution = pd.DataFrame(recent_positions)
                        if contribution.empty:
                            symbol_count = 0
                            top_symbol_share = np.nan
                        else:
                            symbol_pnl = contribution.groupby("symbol")[
                                "portfolio_contribution"
                            ].sum()
                            symbol_count = int(len(symbol_pnl))
                            top_symbol_share = (
                                float(symbol_pnl.max() / total)
                                if total > 0 else np.nan
                            )
                        row = {
                            "variant": name,
                            "gap_tail": tail,
                            "minimum_reclaim_fraction": reclaim,
                            "auction_state": state,
                            "horizon": horizon,
                            "cost_bps_per_side": cost,
                            "full_net_simple_return": total,
                            "standard_max_drawdown": dd,
                            "max_recovery_days": recovery,
                            "recovery_unresolved": unresolved,
                            "selected_events_recent": int(len(contribution)),
                            "symbol_count_recent": symbol_count,
                            "signal_days_recent": int(len(recent)),
                            "post_signal_invalid_days": int(
                                (~recent["valid_signal_day"]).sum()
                            ) if len(recent) else 0,
                            "median_events_per_active_day": (
                                float(
                                    recent.loc[
                                        recent["valid_signal_day"], "event_count"
                                    ].median()
                                )
                                if recent["valid_signal_day"].any()
                                else 0.0
                            ),
                            "top_5_day_profit_share": (
                                float(active["net_pnl"].nlargest(5).sum() / total)
                                if total > 0 else np.nan
                            ),
                            "top_10_day_profit_share": (
                                float(active["net_pnl"].nlargest(10).sum() / total)
                                if total > 0 else np.nan
                            ),
                            "top_symbol_profit_share": top_symbol_share,
                        }
                        for label, start in WINDOW_STARTS.items():
                            subset = monthly[monthly.index >= start.to_period("M")]
                            row[f"average_month_{label}"] = float(subset.mean())
                            row[f"negative_months_{label}"] = int((subset < 0).sum())
                            row[f"zero_months_{label}"] = int((subset == 0).sum())
                        rows.append(row)
                        for month, pnl in monthly.items():
                            monthly_rows.append(
                                {
                                    "variant": name,
                                    "month": str(month),
                                    "net_pnl": float(pnl),
                                }
                            )
                        for block, start, end in BLOCKS:
                            sub = recent[recent["date"].between(start, end)]
                            block_rows.append(
                                {
                                    "variant": name,
                                    "block": block,
                                    "net_pnl": float(sub["net_pnl"].sum()),
                                    "signal_days": int(len(sub)),
                                }
                            )
                        position_rows.extend(valid_position_rows)
    return (
        pd.DataFrame(rows),
        pd.DataFrame(monthly_rows),
        pd.DataFrame(block_rows),
        pd.DataFrame(position_rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals-path", type=Path, required=True)
    parser.add_argument("--event-readiness-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    signals = pd.read_parquet(args.signals_path)
    events = pd.read_parquet(args.event_readiness_path)
    signals["date"] = pd.to_datetime(signals["date"])
    events["date"] = pd.to_datetime(events["date"])
    if signals["date"].max() > CUTOFF or events["date"].max() > CUTOFF:
        raise RuntimeError("Sealed holdout row loaded")
    enriched = enrich_states(signals, events)
    variants, monthly, blocks, positions = evaluate(enriched)
    if len(variants) != 672:
        raise RuntimeError(f"Expected 672 variants, got {len(variants)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    enriched.to_parquet(args.output_dir / "enriched_signals.parquet", index=False)
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    blocks.to_csv(args.output_dir / "blocks.csv", index=False)
    positions.to_parquet(args.output_dir / "positions.parquet", index=False)
    contract = {
        "command": (
            "python campaigns/CAM-0006/src/run0002.py "
            "--signals-path campaigns/CAM-0006/artifacts/RUN-0001/signals.parquet "
            "--event-readiness-path campaigns/CAM-0006/artifacts/readiness/event_readiness.parquet "
            "--output-dir campaigns/CAM-0006/artifacts/RUN-0002"
        ),
        "resolved_defaults": {
            "direction": "long_negative_gap_absorption",
            "gap_tails": list(GAP_TAILS),
            "minimum_reclaim_fractions": list(RECLAIMS),
            "auction_states": list(AUCTION_STATES),
            "horizons": list(HORIZONS),
            "cost_bps_per_side": list(COSTS),
        },
        "executed_variant_count": int(len(variants)),
        "max_loaded_date": str(signals["date"].max().date()),
        "holdout_rows_loaded": 0,
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
