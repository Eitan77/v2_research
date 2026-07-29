from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def causal_state(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    group = frame.sort_values("date").copy()
    returns = group["close_split"].pct_change(fill_method=None)
    prior_close = group["close_split"].shift(1)
    prior_ma20 = group["close_split"].shift(1).rolling(20, min_periods=15).mean()
    prior_rv20 = returns.shift(1).rolling(20, min_periods=15).std()
    causal_rv_median = prior_rv20.shift(1).expanding(min_periods=60).median()
    return pd.DataFrame(
        {
            "date": group["date"],
            f"{label}_trend_up": prior_close.gt(prior_ma20),
            f"{label}_high_vol": prior_rv20.gt(causal_rv_median),
            f"{label}_gap_up": group["open_split"].div(prior_close).sub(1).gt(0),
            f"{label}_state_complete": (
                prior_ma20.notna()
                & prior_rv20.notna()
                & causal_rv_median.notna()
            ),
        }
    )


def grouped(frame: pd.DataFrame, columns: list[str]) -> list[dict]:
    output = []
    for key, group in frame.groupby(columns, dropna=False):
        values = key if isinstance(key, tuple) else (key,)
        row = {column: bool(value) for column, value in zip(columns, values)}
        row.update(
            {
                "trades": int(len(group)),
                "days": int(group["date"].nunique()),
                "net_pnl": float(group["trade_pnl"].sum()),
                "recent_15m_net_pnl": float(
                    group.loc[
                        group["date"].ge(pd.Timestamp("2025-02-01")),
                        "trade_pnl",
                    ].sum()
                ),
                "average_trade_pnl": float(group["trade_pnl"].mean()),
            }
        )
        output.append(row)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", type=Path, required=True)
    parser.add_argument("--daily-state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    trades = pd.read_parquet(args.trades)
    daily = pd.read_parquet(args.daily_state)
    trades["date"] = pd.to_datetime(trades["date"])
    daily["date"] = pd.to_datetime(daily["date"])
    qqq = causal_state(daily[daily["symbol"].eq("QQQ")], "qqq")
    smh = causal_state(daily[daily["symbol"].eq("SMH")], "smh")
    states = qqq.merge(smh, on="date", how="inner", validate="one_to_one")
    complete = states["qqq_state_complete"] & states["smh_state_complete"]
    merged = trades.merge(
        states[complete],
        on="date",
        how="inner",
        validate="many_to_one",
    )
    by_day = trades.groupby("date")["trade_pnl"].sum().sort_values(ascending=False)
    by_event = trades.groupby("event_id")["trade_pnl"].sum().sort_values(
        ascending=False
    )
    by_month = (
        trades.assign(month=trades["date"].dt.to_period("M").astype(str))
        .groupby("month")["trade_pnl"]
        .sum()
        .sort_values(ascending=False)
    )
    report = {
        "status": "passed",
        "state_complete_trades": int(len(merged)),
        "state_attrition_trades": int(len(trades) - len(merged)),
        "qqq_smh_trend": grouped(
            merged, ["qqq_trend_up", "smh_trend_up"]
        ),
        "qqq_volatility": grouped(merged, ["qqq_high_vol"]),
        "qqq_gap": grouped(merged, ["qqq_gap_up"]),
        "concentration": {
            "total_net_pnl": float(trades["trade_pnl"].sum()),
            "best_month": str(by_month.index[0]),
            "best_month_net_pnl": float(by_month.iloc[0]),
            "worst_month": str(by_month.index[-1]),
            "worst_month_net_pnl": float(by_month.iloc[-1]),
            "top_day": str(pd.Timestamp(by_day.index[0]).date()),
            "top_day_net_pnl": float(by_day.iloc[0]),
            "top_two_day_net_pnl": float(by_day.iloc[:2].sum()),
            "top_event": str(by_event.index[0]),
            "top_event_net_pnl": float(by_event.iloc[0]),
            "without_best_month_net_pnl": float(
                trades["trade_pnl"].sum() - by_month.iloc[0]
            ),
            "recent_15m_average_month_without_best_month": float(
                (
                    trades.loc[
                        trades["date"].ge(pd.Timestamp("2025-02-01")),
                        "trade_pnl",
                    ].sum()
                    - by_month.iloc[0]
                )
                / 14
            ),
        },
    }
    args.output.write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
