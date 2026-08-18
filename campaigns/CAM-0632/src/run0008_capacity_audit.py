from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0008"
SOURCE = ROOT / "campaigns" / "CAM-0632" / "artifacts" / "RUN-0006" / "trade_audit.csv"
VARIANTS = [
    "QQQ_TQQQ_SQQQ_reversal_t40_h15_one_bar_opposite_ov0",
    "SMH_SOXL_SOXS_reversal_t50_h20_none_ov20",
]
PARTICIPATION = [0.05, 0.10, 0.25, 1.00]
ACCOUNT_NOTIONALS = [2_000, 10_000, 25_000, 50_000]
ROUND_LOT_SHARES = 100.0
SLEEVE = 0.50


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    trades = pd.read_csv(SOURCE)
    trades = trades[trades.variant.isin(VARIANTS)].copy()
    rows = []
    detail = []
    for participation in PARTICIPATION:
        frame = trades.copy()
        frame["participation"] = participation
        frame["entry_supported_notional"] = frame.entry_ask * frame.entry_ask_size_lots * ROUND_LOT_SHARES * participation
        frame["exit_supported_notional"] = frame.exit_bid * frame.exit_bid_size_lots * ROUND_LOT_SHARES * participation
        frame["roundtrip_supported_sleeve_notional"] = frame[["entry_supported_notional", "exit_supported_notional"]].min(axis=1)
        frame["roundtrip_supported_portfolio_notional"] = frame.roundtrip_supported_sleeve_notional / SLEEVE
        detail.append(frame[["trade_id", "variant", "date", "symbol", "participation", "entry_supported_notional", "exit_supported_notional", "roundtrip_supported_sleeve_notional", "roundtrip_supported_portfolio_notional"]])
        for variant_label, group in [("combined", frame), *list(frame.groupby("variant"))]:
            supported = group.roundtrip_supported_portfolio_notional
            row = {
                "variant": variant_label,
                "participation": participation,
                "trades": len(group),
                "capacity_min_usd": float(supported.min()),
                "capacity_p10_usd": float(supported.quantile(0.10)),
                "capacity_median_usd": float(supported.median()),
                "capacity_p90_usd": float(supported.quantile(0.90)),
            }
            for notional in ACCOUNT_NOTIONALS:
                row[f"fraction_supporting_{notional}_account"] = float((supported >= notional).mean())
            rows.append(row)
    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "capacity_summary.csv", index=False)
    pd.concat(detail, ignore_index=True).to_csv(OUT / "trade_capacity.csv", index=False)
    report = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "size_unit_shares": ROUND_LOT_SHARES,
        "sleeve_fraction": SLEEVE,
        "audited_candidate_trades": len(trades),
        "summary": json.loads(summary.to_json(orient="records")),
        "interpretation_rule": "capacity is an ex-post top-of-book diagnostic and never a filter on strategy PnL",
        "maximum_loaded_date": "2026-04-30",
        "holdout_rows_loaded": 0,
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
