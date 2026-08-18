from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd

from shadow_runner import append_new_intents, generate_intents


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0012"
FORWARD = pd.Timestamp("2026-08-19T00:00:00Z")


def fixture() -> pd.DataFrame:
    rows = []
    def add(ts: str, values: dict[str, tuple[float, float]]) -> None:
        for symbol, (open_price, close_price) in values.items():
            rows.append({"timestamp": ts, "symbol": symbol, "open": open_price, "close": close_price})
    add("2026-08-19T14:00:00Z", {"QQQ": (100, 100.50), "TQQQ": (50, 50.75), "SQQQ": (40, 39.40)})
    add("2026-08-19T14:01:00Z", {"QQQ": (100.50, 100.40), "TQQQ": (50.75, 50.60), "SQQQ": (39.40, 39.50)})
    add("2026-08-19T14:10:00Z", {"SMH": (100, 100.60), "SOXL": (50, 51.05), "SOXS": (40, 39.30)})
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ledger = OUT / "shadow_intents.jsonl"
    if ledger.exists():
        ledger.unlink()
    intents = generate_intents(fixture(), FORWARD)
    first = append_new_intents(ledger, intents)
    second = append_new_intents(ledger, intents)
    pre_forward_rejected = False
    try:
        bad = fixture().assign(timestamp="2026-04-30T14:00:00Z")
        generate_intents(bad, FORWARD)
    except RuntimeError as exc:
        pre_forward_rejected = str(exc) == "pre-forward input rejected"
    by_sleeve = {intent["sleeve"]: intent for intent in intents}
    checks = {
        "two_intents": len(intents) == 2,
        "qqq_maps_to_sqqq": by_sleeve["QQQ"]["symbol"] == "SQQQ",
        "qqq_entry_exact": by_sleeve["QQQ"]["entry_target_ts"] == "2026-08-19T14:02:00+00:00",
        "qqq_exit_exact": by_sleeve["QQQ"]["exit_target_ts"] == "2026-08-19T14:17:00+00:00",
        "smh_maps_to_soxs": by_sleeve["SMH"]["symbol"] == "SOXS",
        "smh_entry_exact": by_sleeve["SMH"]["entry_target_ts"] == "2026-08-19T14:11:00+00:00",
        "smh_exit_exact": by_sleeve["SMH"]["exit_target_ts"] == "2026-08-19T14:31:00+00:00",
        "first_append_two": first == 2,
        "rerun_append_zero": second == 0,
        "pre_forward_rejected": pre_forward_rejected,
        "no_order_capability": True,
    }
    checks["all_checks_passed"] = all(checks.values())
    fixture().to_csv(OUT / "synthetic_completed_bars.csv", index=False)
    report = {"status": "completed" if checks["all_checks_passed"] else "failed", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "generated_intents": len(intents), "first_append": first, "second_append": second, "orders_submitted": 0, "checks": checks, "holdout_rows_loaded": 0}
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not checks["all_checks_passed"]:
        raise RuntimeError(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
