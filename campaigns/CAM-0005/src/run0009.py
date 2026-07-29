from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cam0005 import CUTOFF, max_drawdown_and_recovery, rolling_prior_quantile


SLIPPAGES = (2, 5, 10)
BLOCKS = (
    ("block_1", pd.Timestamp("2024-11-01"), pd.Timestamp("2025-04-30")),
    ("block_2", pd.Timestamp("2025-05-01"), pd.Timestamp("2025-10-31")),
    ("block_3", pd.Timestamp("2025-11-01"), pd.Timestamp("2026-04-30")),
)


def prepare_states(
    features: pd.DataFrame, minutes: pd.DataFrame, daily: pd.DataFrame
) -> pd.DataFrame:
    feature = features.copy()
    feature["session"] = pd.to_datetime(feature["session"])
    smh = feature[feature["pair"].eq("smh")].sort_values("session").copy()
    qqq = (
        feature[feature["pair"].eq("qqq")]
        .sort_values("session")[["session", "signal_return"]]
        .rename(columns={"signal_return": "qqq_signal_return"})
    )
    smh["vol_q50"] = rolling_prior_quantile(smh["realized_vol"], 0.50)
    smh["vol_q67"] = rolling_prior_quantile(smh["realized_vol"], 0.67)
    smh["volume_q50"] = rolling_prior_quantile(smh["dollar_volume"], 0.50)
    smh["volume_q67"] = rolling_prior_quantile(smh["dollar_volume"], 0.67)
    smh = smh.merge(qqq, on="session", how="left", validate="one_to_one")

    minute = minutes.copy()
    minute["session"] = pd.to_datetime(minute["session"])
    pre = (
        minute[
            minute["symbol"].isin(["SMH", "QQQ"])
            & minute["minute"].isin(["09:30", "15:00"])
        ]
        .pivot_table(
            index=["session", "symbol"], columns="minute", values="open", aggfunc="first"
        )
        .reset_index()
    )
    pre["pre_signal_return"] = pre["15:00"] / pre["09:30"] - 1
    smh_pre = pre[pre["symbol"].eq("SMH")][
        ["session", "15:00", "pre_signal_return"]
    ].rename(
        columns={"15:00": "smh_1500", "pre_signal_return": "smh_pre_signal_return"}
    )
    qqq_pre = pre[pre["symbol"].eq("QQQ")][["session", "15:00"]].rename(
        columns={"15:00": "qqq_1500"}
    )
    smh = smh.merge(smh_pre, on="session", how="left", validate="one_to_one")
    smh = smh.merge(qqq_pre, on="session", how="left", validate="one_to_one")

    day = daily[daily["adjustment"].eq("split")].copy()
    day["date"] = pd.to_datetime(day["date"])
    daily_states = []
    for symbol in ("SMH", "QQQ"):
        frame = day[day["symbol"].eq(symbol)].sort_values("date").copy()
        frame[f"{symbol.lower()}_prior5_return"] = (
            frame["close"].shift(1) / frame["close"].shift(6) - 1
        )
        frame[f"{symbol.lower()}_prior_sma20"] = (
            frame["close"].shift(1).rolling(20, min_periods=20).mean()
        )
        daily_states.append(
            frame[
                [
                    "date",
                    f"{symbol.lower()}_prior5_return",
                    f"{symbol.lower()}_prior_sma20",
                ]
            ]
        )
    state = daily_states[0].merge(
        daily_states[1], on="date", how="inner", validate="one_to_one"
    ).rename(columns={"date": "session"})
    smh = smh.merge(state, on="session", how="left", validate="one_to_one")
    smh["is_q67"] = smh["abs_signal"].ge(smh["threshold_q67"])
    smh["is_q80"] = smh["abs_signal"].ge(smh["threshold_q80"])
    smh["edge20"] = (
        (smh["signal_return"].lt(0) & smh["close_location"].le(0.20))
        | (smh["signal_return"].gt(0) & smh["close_location"].ge(0.80))
    )
    smh["vol_high50"] = smh["realized_vol"].ge(smh["vol_q50"])
    smh["vol_low50"] = smh["realized_vol"].lt(smh["vol_q50"])
    smh["vol_high67"] = smh["realized_vol"].ge(smh["vol_q67"])
    smh["volume_high50"] = smh["dollar_volume"].ge(smh["volume_q50"])
    smh["volume_high67"] = smh["dollar_volume"].ge(smh["volume_q67"])
    smh["qqq_same"] = np.sign(smh["signal_return"]).eq(
        np.sign(smh["qqq_signal_return"])
    )
    smh["qqq_opposite"] = ~smh["qqq_same"]
    smh["day_extension"] = np.sign(smh["signal_return"]).eq(
        np.sign(smh["smh_pre_signal_return"])
    )
    smh["day_reversal"] = ~smh["day_extension"]
    smh["prior5_same"] = np.sign(smh["signal_return"]).eq(
        np.sign(smh["smh_prior5_return"])
    )
    smh["prior5_opposite"] = ~smh["prior5_same"]
    smh["smh_above_sma20"] = smh["smh_1500"].ge(smh["smh_prior_sma20"])
    smh["smh_below_sma20"] = smh["smh_1500"].lt(smh["smh_prior_sma20"])
    smh["qqq_above_sma20"] = smh["qqq_1500"].ge(smh["qqq_prior_sma20"])
    smh["qqq_below_sma20"] = smh["qqq_1500"].lt(smh["qqq_prior_sma20"])
    return smh


def masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    q50 = frame["is_q50"].astype(bool)
    q60 = frame["is_q60"].astype(bool)
    return {
        "q50_base": q50,
        "q60_base": q60,
        "q60_edge20": q60 & frame["edge20"],
        "q67_base": q60 & frame["is_q67"],
        "q80_base": q60 & frame["is_q80"],
        "q60_vol_high50": q60 & frame["vol_high50"],
        "q60_vol_low50": q60 & frame["vol_low50"],
        "q60_vol_high67": q60 & frame["vol_high67"],
        "q60_volume_high50": q60 & frame["volume_high50"],
        "q60_volume_high67": q60 & frame["volume_high67"],
        "q60_qqq_same": q60 & frame["qqq_same"],
        "q60_qqq_opposite": q60 & frame["qqq_opposite"],
        "q60_day_extension": q60 & frame["day_extension"],
        "q60_day_reversal": q60 & frame["day_reversal"],
        "q60_prior5_same": q60 & frame["prior5_same"],
        "q60_prior5_opposite": q60 & frame["prior5_opposite"],
        "q60_smh_above_sma20": q60 & frame["smh_above_sma20"],
        "q60_smh_below_sma20": q60 & frame["smh_below_sma20"],
        "q60_qqq_above_sma20": q60 & frame["qqq_above_sma20"],
        "q60_qqq_below_sma20": q60 & frame["qqq_below_sma20"],
        "q60_edge20_vol_high50": q60 & frame["edge20"] & frame["vol_high50"],
        "q60_edge20_qqq_same": q60 & frame["edge20"] & frame["qqq_same"],
        "q60_edge20_day_extension": q60 & frame["edge20"] & frame["day_extension"],
        "q67_volume_high50": q60 & frame["is_q67"] & frame["volume_high50"],
    }


def evaluate(
    frame: pd.DataFrame, filter_masks: dict[str, pd.Series]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows: list[dict] = []
    monthly_rows: list[dict] = []
    block_rows: list[dict] = []
    months = pd.period_range("2024-11", "2026-04", freq="M")
    for name, mask in filter_masks.items():
        base = frame[mask.fillna(False)].copy()
        for slippage in SLIPPAGES:
            selected = base.copy()
            selected["net_pnl"] = (
                selected["nbbo_gross_return"] - 2 * slippage / 10_000
            )
            daily = selected.groupby("date", as_index=False)["net_pnl"].sum()
            monthly = (
                daily.assign(month=pd.to_datetime(daily["date"]).dt.to_period("M"))
                .groupby("month")["net_pnl"]
                .sum()
                .reindex(months, fill_value=0.0)
            )
            dd, recovery, unresolved = max_drawdown_and_recovery(daily)
            total = float(daily["net_pnl"].sum())
            variant = f"{name}_slip{slippage}"
            rows.append(
                {
                    "variant": variant,
                    "filter": name,
                    "additional_slippage_bps_per_side": slippage,
                    "trade_count": int(len(selected)),
                    "attrition_vs_q60": (
                        float(1 - len(selected) / frame["is_q60"].sum())
                        if name != "q50_base" else np.nan
                    ),
                    "full_net_simple_return": total,
                    "average_month_18m": float(monthly.mean()),
                    "median_month_18m": float(monthly.median()),
                    "positive_months_18m": int((monthly > 0).sum()),
                    "negative_months_18m": int((monthly < 0).sum()),
                    "zero_months_18m": int((monthly == 0).sum()),
                    "standard_max_drawdown": dd,
                    "max_recovery_days": recovery,
                    "recovery_unresolved": unresolved,
                    "win_rate": float((selected["net_pnl"] > 0).mean()),
                    "top_5_day_profit_share": (
                        float(daily["net_pnl"].nlargest(5).sum() / total)
                        if total > 0 else np.nan
                    ),
                    "soxl_net": float(
                        selected.loc[selected["symbol"].eq("SOXL"), "net_pnl"].sum()
                    ),
                    "soxs_net": float(
                        selected.loc[selected["symbol"].eq("SOXS"), "net_pnl"].sum()
                    ),
                }
            )
            for month, pnl in monthly.items():
                monthly_rows.append(
                    {"variant": variant, "month": str(month), "net_pnl": float(pnl)}
                )
            for block, start, end in BLOCKS:
                sub = selected[pd.to_datetime(selected["date"]).between(start, end)]
                block_rows.append(
                    {
                        "variant": variant,
                        "block": block,
                        "trade_count": int(len(sub)),
                        "net_pnl": float(sub["net_pnl"].sum()),
                    }
                )
    return pd.DataFrame(rows), pd.DataFrame(monthly_rows), pd.DataFrame(block_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-path", type=Path, required=True)
    parser.add_argument("--features-path", type=Path, required=True)
    parser.add_argument("--minutes-path", type=Path, required=True)
    parser.add_argument("--daily-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    replay = pd.read_parquet(args.replay_path)
    features = pd.read_parquet(args.features_path)
    minutes = pd.read_parquet(args.minutes_path)
    daily = pd.read_parquet(args.daily_path)
    for frame, column in (
        (replay, "next_session"),
        (features, "next_session"),
        (minutes, "session"),
        (daily, "date"),
    ):
        if pd.to_datetime(frame[column]).max() > CUTOFF:
            raise RuntimeError(f"Sealed holdout row loaded from {column}")
    state = prepare_states(features, minutes, daily)
    replay["date"] = pd.to_datetime(replay["date"])
    enriched = replay.merge(
        state,
        left_on="date",
        right_on="session",
        how="left",
        validate="many_to_one",
        suffixes=("", "_feature"),
    )
    required = [
        "realized_vol", "dollar_volume", "qqq_signal_return",
        "smh_pre_signal_return", "smh_prior5_return", "smh_prior_sma20",
        "qqq_prior_sma20",
    ]
    completeness = {
        column: int(enriched.loc[enriched["is_q60"], column].notna().sum())
        for column in required
    }
    if any(count != int(enriched["is_q60"].sum()) for count in completeness.values()):
        raise RuntimeError(f"Silent q60 feature attrition: {completeness}")
    filter_masks = masks(enriched)
    if len(filter_masks) != 24:
        raise RuntimeError(f"Expected 24 filters, got {len(filter_masks)}")
    variants, monthly, blocks = evaluate(enriched, filter_masks)
    if len(variants) != 72:
        raise RuntimeError(f"Expected 72 variants, got {len(variants)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    enriched.to_parquet(args.output_dir / "enriched_events.parquet", index=False)
    variants.to_csv(args.output_dir / "variants.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv", index=False)
    blocks.to_csv(args.output_dir / "blocks.csv", index=False)
    attrition = pd.DataFrame(
        [
            {
                "filter": name,
                "trade_count": int(mask.fillna(False).sum()),
                "q60_base_count": int(enriched["is_q60"].sum()),
                "attrition_vs_q60": (
                    1 - mask.fillna(False).sum() / enriched["is_q60"].sum()
                    if name != "q50_base" else np.nan
                ),
            }
            for name, mask in filter_masks.items()
        ]
    )
    attrition.to_csv(args.output_dir / "attrition.csv", index=False)
    contract = {
        "command": (
            "python campaigns/CAM-0005/src/run0009.py "
            "--replay-path campaigns/CAM-0005/artifacts/RUN-0008/event_replay.parquet "
            "--features-path campaigns/CAM-0005/artifacts/RUN-0002/features.parquet "
            "--minutes-path campaigns/CAM-0005/artifacts/readiness/targeted_minutes.parquet "
            "--daily-path campaigns/CAM-0005/artifacts/readiness/split_daily.parquet "
            "--output-dir campaigns/CAM-0005/artifacts/RUN-0009"
        ),
        "resolved_defaults": {
            "filter_count": len(filter_masks),
            "additional_slippage_bps_per_side": list(SLIPPAGES),
            "rolling_window_sessions": 60,
            "rolling_minimum_sessions": 40,
            "rolling_thresholds_shifted": True,
        },
        "executed_variant_count": int(len(variants)),
        "q60_feature_completeness": completeness,
        "holdout_rows_loaded": 0,
    }
    (args.output_dir / "contract.json").write_text(
        json.dumps(contract, indent=2), encoding="utf-8"
    )
    central = variants[variants["additional_slippage_bps_per_side"].eq(5)]
    print(
        central.sort_values(
            ["average_month_18m", "standard_max_drawdown"],
            ascending=[False, True],
        ).head(24).to_string(index=False)
    )


if __name__ == "__main__":
    main()
