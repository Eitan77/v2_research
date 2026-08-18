from __future__ import annotations

import numpy as np
import pandas as pd


QQQ_VARIANT = "QQQ_TQQQ_SQQQ_reversal_t40_h15_one_bar_opposite_ov0"
SMH_VARIANT = "SMH_SOXL_SOXS_reversal_t50_h20_none_ov20"


def _emit(context: dict, *, pair: str, underlying: str, bull: str, inverse: str,
          threshold: float, hold: int, confirmation: bool, overshoot: float) -> list[dict]:
    times = context["times"]
    opens = context["open"]
    closes = context["close"]
    hhmm = context["local_hhmm"]
    delay = 2 if confirmation else 1
    index = np.arange(len(times))
    valid = (index + delay + hold - 1 < len(times)) & (hhmm >= "09:35") & (hhmm <= "15:35")
    rows = []
    last_exit = -1
    for signal_i in index[valid]:
        underlying_return = closes[underlying][signal_i] / opens[underlying][signal_i] - 1
        if abs(underlying_return) < threshold:
            continue
        if confirmation:
            confirm_return = closes[underlying][signal_i + 1] / opens[underlying][signal_i + 1] - 1
            if np.sign(confirm_return) != -np.sign(underlying_return):
                continue
        else:
            if underlying_return > 0:
                directional_overshoot = closes[bull][signal_i] / opens[bull][signal_i] - 1 - 3 * underlying_return
            else:
                directional_overshoot = closes[inverse][signal_i] / opens[inverse][signal_i] - 1 + 3 * underlying_return
            if directional_overshoot < overshoot:
                continue
        entry_i = int(signal_i + delay)
        exit_i = entry_i + hold - 1
        if entry_i <= last_exit:
            continue
        symbol = inverse if underlying_return > 0 else bull
        variant = QQQ_VARIANT if pair == "QQQ_TQQQ_SQQQ" else SMH_VARIANT
        rows.append({
            "variant": variant,
            "date": context["date"],
            "symbol": symbol,
            "signal_ts": times[signal_i],
            "entry_ts": times[entry_i],
            "exit_ts": times[exit_i] + pd.Timedelta(minutes=1),
            "gross_return": closes[symbol][exit_i] / opens[symbol][entry_i] - 1,
        })
        last_exit = exit_i
    return rows


def generate_frozen_trades(contexts: dict[str, list[dict]]) -> pd.DataFrame:
    rows = []
    for context in contexts["QQQ_TQQQ_SQQQ"]:
        rows.extend(_emit(context, pair="QQQ_TQQQ_SQQQ", underlying="QQQ", bull="TQQQ", inverse="SQQQ", threshold=0.0040, hold=15, confirmation=True, overshoot=0.0))
    for context in contexts["SMH_SOXL_SOXS"]:
        rows.extend(_emit(context, pair="SMH_SOXL_SOXS", underlying="SMH", bull="SOXL", inverse="SOXS", threshold=0.0050, hold=20, confirmation=False, overshoot=0.0020))
    return pd.DataFrame(rows).sort_values(["variant", "entry_ts", "symbol"]).reset_index(drop=True)
