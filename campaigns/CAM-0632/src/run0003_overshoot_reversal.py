from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from run0002_structural_scalps import PAIRS, build_contexts, load_bars, metrics


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0003"
THRESHOLDS = [30, 40, 50, 60]
HOLDS = [10, 15, 20]
CONFIRMATIONS = ["none", "one_bar_opposite"]
OVERSHOOTS = [0, 10, 20]
COSTS = [2, 5, 10]


def variant_trades(contexts: dict, pair: tuple, threshold_bp: int, hold: int, confirmation: str, overshoot_bp: int) -> pd.DataFrame:
    underlying, bull, inverse, leverage = pair
    pair_name = "_".join(pair[:3])
    threshold = threshold_bp / 10_000
    overshoot_min = overshoot_bp / 10_000
    rows = []
    delay = 1 if confirmation == "none" else 2
    for context in contexts[pair_name]:
        times = context["times"]
        opens = context["open"]
        closes = context["close"]
        hhmm = context["local_hhmm"]
        n = len(times)
        t = np.arange(n)
        valid = (t + delay + hold - 1 < n) & (hhmm >= "09:35") & (hhmm <= "15:35")
        idx = t[valid]
        uret = closes[underlying][idx] / opens[underlying][idx] - 1
        bull_ret = closes[bull][idx] / opens[bull][idx] - 1
        inverse_ret = closes[inverse][idx] / opens[inverse][idx] - 1
        signal = np.abs(uret) >= threshold
        selected_symbol = np.where(uret > 0, inverse, bull)
        directional_overshoot = np.where(uret > 0, bull_ret - leverage * uret, inverse_ret + leverage * uret)
        if overshoot_bp > 0:
            signal &= directional_overshoot >= overshoot_min
        if confirmation == "one_bar_opposite":
            confirm_ret = closes[underlying][idx + 1] / opens[underlying][idx + 1] - 1
            signal &= np.sign(confirm_ret) == -np.sign(uret)
        candidates = np.flatnonzero(signal)
        last_exit = -1
        for pos in candidates:
            signal_i = int(idx[pos])
            entry_i = signal_i + delay
            exit_i = entry_i + hold - 1
            if entry_i <= last_exit:
                continue
            symbol = str(selected_symbol[pos])
            entry = opens[symbol][entry_i]
            exit_price = closes[symbol][exit_i]
            rows.append({
                "date": context["date"],
                "pair": pair_name,
                "symbol": symbol,
                "signal_ts": times[signal_i],
                "entry_ts": times[entry_i],
                "exit_ts": times[exit_i] + pd.Timedelta(minutes=1),
                "gross_return": exit_price / entry - 1,
                "signal_strength": abs(float(uret[pos])),
                "directional_overshoot": float(directional_overshoot[pos]),
            })
            last_exit = exit_i
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    contexts, calendar, attrition = build_contexts(load_bars())
    variants = [(pair, threshold, hold, confirmation, overshoot) for pair in PAIRS for threshold in THRESHOLDS for hold in HOLDS for confirmation in CONFIRMATIONS for overshoot in OVERSHOOTS]
    rows = []
    for number, (pair, threshold, hold, confirmation, overshoot) in enumerate(variants, 1):
        pair_name = "_".join(pair[:3])
        variant = f"{pair_name}_reversal_t{threshold}_h{hold}_{confirmation}_ov{overshoot}"
        trades = variant_trades(contexts, pair, threshold, hold, confirmation, overshoot)
        for cost in COSTS:
            result, _ = metrics(trades, calendar, cost)
            rows.append({"variant": variant, "pair": pair_name, "threshold_bp": threshold, "hold_min": hold, "confirmation": confirmation, "overshoot_bp": overshoot, **result})
        if number % 24 == 0:
            print(f"variants {number}/{len(variants)}", flush=True)
    leaderboard = pd.DataFrame(rows)
    leaderboard.to_csv(OUT / "leaderboard.csv", index=False)
    five = leaderboard[(leaderboard.cost_bp_side == 5) & (leaderboard.trades >= 100)].copy()
    five["all_blocks_positive"] = five.block_returns.apply(lambda values: all(value > 0 for value in values))
    top = five.sort_values(["all_blocks_positive", "recent12_return", "net_return"], ascending=False).head(10)
    top.to_csv(OUT / "top_5bp_candidates.csv", index=False)
    paths = []
    ledgers = []
    for row in top.itertuples():
        pair = next(pair for pair in PAIRS if "_".join(pair[:3]) == row.pair)
        trades = variant_trades(contexts, pair, int(row.threshold_bp), int(row.hold_min), row.confirmation, int(row.overshoot_bp))
        trades["variant"] = row.variant
        ledgers.append(trades)
        for cost in COSTS:
            _, daily = metrics(trades, calendar, cost)
            for period_type, grouped in [("weekly", daily.groupby(daily.index.to_period("W-FRI")).sum()), ("monthly", daily.groupby(daily.index.to_period("M")).sum()), ("yearly", daily.groupby(daily.index.to_period("Y")).sum())]:
                paths.extend({"variant": row.variant, "cost_bp_side": cost, "period_type": period_type, "period": str(period), "net_return": float(value)} for period, value in grouped.items())
    pd.DataFrame(paths).to_csv(OUT / "top_period_paths.csv", index=False)
    if ledgers:
        pd.concat(ledgers, ignore_index=True).to_csv(OUT / "top_trade_ledgers.csv", index=False)
    all_block = five[five.all_blocks_positive]
    report = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "planned_signal_variants": len(variants),
        "executed_signal_variants": int(leaderboard.variant.nunique()),
        "planned_costed_rows": len(variants) * len(COSTS),
        "executed_costed_rows": len(leaderboard),
        "attrition": attrition,
        "five_bp_all_block_positive_count": len(all_block),
        "five_bp_all_block_positive_recent12_positive_count": int((all_block.recent12_return > 0).sum()),
        "best_five_bp": json.loads(five.sort_values(["all_blocks_positive", "recent12_return", "net_return"], ascending=False).head(5).to_json(orient="records")),
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
        "quote_replay_performed": False,
        "decision_gate": "quote_replay_only_if_stable_neighborhood_improves_recent_and_consistency_profile",
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
