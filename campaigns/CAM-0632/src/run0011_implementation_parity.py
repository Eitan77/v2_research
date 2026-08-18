from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from frozen_candidate import QQQ_VARIANT, SMH_VARIANT, generate_frozen_trades
from run0002_structural_scalps import build_contexts, load_bars


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0011"
SOURCE = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0003" / "top_trade_ledgers.csv"
CUTOFF = pd.Timestamp("2026-04-30")
KEYS = ["variant", "date", "symbol", "signal_ts", "entry_ts", "exit_ts"]


def semantic_checks(frame: pd.DataFrame) -> dict:
    checks = {
        "expected_trade_count": len(frame) == 314,
        "both_variants_present": set(frame.variant) == {QQQ_VARIANT, SMH_VARIANT},
        "both_bull_and_inverse_symbols_present": {"TQQQ", "SQQQ", "SOXL", "SOXS"}.issubset(set(frame.symbol)),
        "maximum_date_at_or_before_cutoff": pd.to_datetime(frame.date).max() <= CUTOFF,
        "entry_after_signal": bool((frame.entry_ts > frame.signal_ts).all()),
        "exit_after_entry": bool((frame.exit_ts > frame.entry_ts).all()),
    }
    overlap_ok = True
    for _, group in frame.groupby("variant"):
        ordered = group.sort_values("entry_ts")
        overlap_ok &= bool((ordered.entry_ts.iloc[1:].reset_index(drop=True) >= ordered.exit_ts.iloc[:-1].reset_index(drop=True)).all())
    checks["no_within_sleeve_overlap"] = overlap_ok
    return checks


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    bars = load_bars()
    contexts, _, attrition = build_contexts(bars)
    actual = generate_frozen_trades(contexts)
    expected = pd.read_csv(SOURCE, parse_dates=["date", "signal_ts", "entry_ts", "exit_ts"])
    expected = expected[expected.variant.isin([QQQ_VARIANT, SMH_VARIANT])].drop_duplicates(KEYS).sort_values(["variant", "entry_ts", "symbol"]).reset_index(drop=True)
    for frame in (actual, expected):
        frame["date"] = pd.to_datetime(frame.date)
        for column in ["signal_ts", "entry_ts", "exit_ts"]:
            frame[column] = pd.to_datetime(frame[column], utc=True)
    merged = actual.merge(expected, on=KEYS, how="outer", suffixes=("_actual", "_expected"), indicator=True)
    missing_or_extra = merged[merged._merge != "both"]
    paired = merged[merged._merge == "both"].copy()
    paired["gross_return_abs_difference"] = (paired.gross_return_actual - paired.gross_return_expected).abs()
    checks = semantic_checks(actual)
    checks["exact_key_parity"] = len(missing_or_extra) == 0
    checks["gross_return_tolerance"] = bool((paired.gross_return_abs_difference <= 1e-12).all())
    checks["all_checks_passed"] = all(checks.values())
    actual.to_csv(OUT / "independent_trade_ledger.csv", index=False)
    missing_or_extra.to_csv(OUT / "key_mismatches.csv", index=False)
    paired[[*KEYS, "gross_return_actual", "gross_return_expected", "gross_return_abs_difference"]].to_csv(OUT / "return_parity.csv", index=False)
    report = {
        "status": "completed" if checks["all_checks_passed"] else "failed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "expected_trades": len(expected),
        "actual_trades": len(actual),
        "key_mismatches": len(missing_or_extra),
        "maximum_return_difference": float(paired.gross_return_abs_difference.max()),
        "checks": checks,
        "attrition": attrition,
        "maximum_signal_date": str(pd.to_datetime(actual.date).max().date()),
        "maximum_loaded_date": str(pd.to_datetime(bars.date).max().date()),
        "holdout_rows_loaded": 0,
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not checks["all_checks_passed"]:
        raise RuntimeError(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
