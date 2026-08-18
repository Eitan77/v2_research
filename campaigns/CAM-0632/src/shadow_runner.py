from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


REQUIRED = {"timestamp", "symbol", "open", "close"}
QQQ = {"QQQ", "TQQQ", "SQQQ"}
SMH = {"SMH", "SOXL", "SOXS"}


def _event_id(variant: str, signal_ts: pd.Timestamp, symbol: str) -> str:
    payload = f"CAM-0632|{variant}|{signal_ts.isoformat()}|{symbol}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _return(frame: pd.DataFrame, symbol: str) -> float:
    row = frame.loc[symbol]
    return float(row.close / row.open - 1)


def generate_intents(bars: pd.DataFrame, forward_start: pd.Timestamp) -> list[dict]:
    missing = REQUIRED - set(bars.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    work = bars.copy()
    work["timestamp"] = pd.to_datetime(work.timestamp, utc=True)
    start = pd.Timestamp(forward_start)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    else:
        start = start.tz_convert("UTC")
    if work.timestamp.min() < start:
        raise RuntimeError("pre-forward input rejected")
    if work.duplicated(["timestamp", "symbol"]).any():
        raise RuntimeError("duplicate completed-bar key")
    grouped = {timestamp: group.set_index("symbol") for timestamp, group in work.groupby("timestamp", sort=True)}
    intents = []
    active_until = {"QQQ": pd.Timestamp.min.tz_localize("UTC"), "SMH": pd.Timestamp.min.tz_localize("UTC")}
    for timestamp in sorted(grouped):
        local_hhmm = timestamp.tz_convert("America/New_York").strftime("%H:%M")
        if not ("09:35" <= local_hhmm <= "15:35"):
            continue
        current = grouped[timestamp]
        if SMH.issubset(current.index):
            underlying = _return(current, "SMH")
            if abs(underlying) >= 0.005:
                if underlying > 0:
                    symbol = "SOXS"
                    overshoot = _return(current, "SOXL") - 3 * underlying
                else:
                    symbol = "SOXL"
                    overshoot = _return(current, "SOXS") + 3 * underlying
                entry = timestamp + pd.Timedelta(minutes=1)
                if overshoot >= 0.002 and entry >= active_until["SMH"]:
                    exit_target = entry + pd.Timedelta(minutes=20)
                    variant = "SMH_SOXL_SOXS_reversal_t50_h20_none_ov20"
                    intents.append({"event_id": _event_id(variant, timestamp, symbol), "campaign_id": "CAM-0632", "variant": variant, "sleeve": "SMH", "symbol": symbol, "signal_ts": timestamp.isoformat(), "entry_target_ts": entry.isoformat(), "exit_target_ts": exit_target.isoformat(), "status": "shadow_intent_no_order"})
                    active_until["SMH"] = exit_target
        prior_ts = timestamp - pd.Timedelta(minutes=1)
        if QQQ.issubset(current.index) and prior_ts in grouped and QQQ.issubset(grouped[prior_ts].index):
            prior = grouped[prior_ts]
            shock = _return(prior, "QQQ")
            confirmation = _return(current, "QQQ")
            entry = timestamp + pd.Timedelta(minutes=1)
            if abs(shock) >= 0.004 and confirmation * shock < 0 and entry >= active_until["QQQ"]:
                symbol = "SQQQ" if shock > 0 else "TQQQ"
                exit_target = entry + pd.Timedelta(minutes=15)
                variant = "QQQ_TQQQ_SQQQ_reversal_t40_h15_one_bar_opposite_ov0"
                intents.append({"event_id": _event_id(variant, prior_ts, symbol), "campaign_id": "CAM-0632", "variant": variant, "sleeve": "QQQ", "symbol": symbol, "signal_ts": prior_ts.isoformat(), "confirmation_ts": timestamp.isoformat(), "entry_target_ts": entry.isoformat(), "exit_target_ts": exit_target.isoformat(), "status": "shadow_intent_no_order"})
                active_until["QQQ"] = exit_target
    return intents


def append_new_intents(path: Path, intents: list[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                existing.add(json.loads(line)["event_id"])
    new = [intent for intent in intents if intent["event_id"] not in existing]
    if new:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            for intent in new:
                handle.write(json.dumps(intent, sort_keys=True) + "\n")
    return len(new)


def main() -> None:
    parser = argparse.ArgumentParser(description="CAM-0632 no-order shadow intent generator")
    parser.add_argument("--bars", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--forward-start", required=True)
    args = parser.parse_args()
    intents = generate_intents(pd.read_csv(args.bars), pd.Timestamp(args.forward_start))
    appended = append_new_intents(args.ledger, intents)
    print(json.dumps({"generated": len(intents), "appended": appended, "orders_submitted": 0}))


if __name__ == "__main__":
    main()
