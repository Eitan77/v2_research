from __future__ import annotations

import json
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from adaptation_strategies import build_adaptations
from run_suite import _load_or_build_fundamentals
from suite_core import CAMPAIGNS, load_panels, write_json


SHARED = CAMPAIGNS / "CAM-0600" / "artifacts" / "shared"
START = pd.Timestamp("2025-05-01")
END = pd.Timestamp("2026-04-30")
NY = ZoneInfo("America/New_York")


def target_ts(date: pd.Timestamp, clock: time) -> pd.Timestamp:
    local = datetime.combine(pd.Timestamp(date).date(), clock, tzinfo=NY)
    return pd.Timestamp(local).tz_convert("UTC")


def main() -> None:
    summary = pd.read_csv(SHARED / "adaptation_summary.csv")
    summary = summary[summary["quote_gate"] & summary["selected_executable_variant"].notna()].copy()
    panels = load_panels()
    fundamentals, coverage = _load_or_build_fundamentals(panels)
    rows = []
    campaign_reports = []
    for record in summary.itertuples(index=False):
        variants = build_adaptations(str(record.campaign_id), panels, fundamentals)
        matches = [v for v in variants if v.variant_id == str(record.selected_executable_variant)]
        if len(matches) != 1:
            raise RuntimeError(f"quote variant reconciliation failed for {record.campaign_id}: {len(matches)}")
        variant = matches[0]
        if variant.holding not in {"open_to_next_open", "open_to_close"}:
            raise RuntimeError(f"unsupported quote holding {variant.holding}")
        executed = np.zeros_like(variant.weights)
        if variant.execution_lag == 1:
            executed[1:] = variant.weights[:-1]
        else:
            executed[:] = variant.weights
        if (executed < -1e-12).any():
            raise RuntimeError(f"selected quote variant contains direct short: {record.campaign_id}")
        panel = variant.panel
        active_rows = 0
        active_symbols = set()
        for i, date in enumerate(panel.dates):
            date = pd.Timestamp(date).normalize()
            if date < START or date > END:
                continue
            if variant.holding == "open_to_next_open":
                if i + 1 >= panel.n_dates:
                    continue
                exit_date = pd.Timestamp(panel.dates[i+1]).normalize()
                if exit_date > END:
                    continue
                exit_clock = time(9, 30)
            else:
                exit_date = date
                exit_clock = time(16, 0)
            for col in np.flatnonzero(executed[i] > 1e-12):
                symbol = str(panel.symbols[col])
                weight = float(executed[i, col])
                rows.append({
                    "campaign_id": str(record.campaign_id),
                    "variant_id": variant.variant_id,
                    "session_date": date,
                    "exit_session_date": exit_date,
                    "symbol": symbol,
                    "target_weight": weight,
                    "holding": variant.holding,
                    "entry_target_ts": target_ts(date, time(9, 30)),
                    "exit_target_ts": target_ts(exit_date, exit_clock),
                })
                active_rows += 1
                active_symbols.add(symbol)
        campaign_reports.append({
            "campaign_id": str(record.campaign_id), "variant_id": variant.variant_id,
            "position_rows": active_rows, "symbols": len(active_symbols),
            "minimum_weight": float(executed[executed > 1e-12].min()) if (executed > 1e-12).any() else None,
            "maximum_gross": float(executed.sum(axis=1).max()), "broker_margin": False,
        })
    ledger = pd.DataFrame(rows)
    if ledger.empty:
        raise RuntimeError("quote candidate ledger is empty")
    if (ledger["session_date"] >= pd.Timestamp("2026-05-01")).any() or (ledger["exit_session_date"] >= pd.Timestamp("2026-05-01")).any():
        raise RuntimeError("quote ledger crossed holdout")
    ledger.to_parquet(SHARED / "quote_candidate_positions.parquet", index=False)
    roles = pd.concat([
        ledger[["symbol", "entry_target_ts"]].rename(columns={"entry_target_ts": "target_ts"}).assign(role="entry_ask_after"),
        ledger[["symbol", "exit_target_ts"]].rename(columns={"exit_target_ts": "target_ts"}).assign(role=np.where(ledger["holding"].eq("open_to_close"), "exit_bid_before", "exit_bid_after")),
    ], ignore_index=True).drop_duplicates(["symbol", "target_ts", "role"])
    roles.to_parquet(SHARED / "quote_roles.parquet", index=False)
    report = {
        "status": "passed", "generated_utc": datetime.now(timezone.utc).isoformat(),
        "campaigns": int(ledger["campaign_id"].nunique()), "position_rows": int(len(ledger)),
        "unique_quote_roles": int(len(roles)), "symbols": int(ledger["symbol"].nunique()),
        "minimum_session": str(ledger["session_date"].min().date()),
        "maximum_exit_session": str(ledger["exit_session_date"].max().date()),
        "holdout_rows_loaded": 0, "broker_margin": False,
        "fundamental_coverage_reconciled": list(coverage), "campaign_reports": campaign_reports,
    }
    write_json(SHARED / "quote_ledger_report.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
