from __future__ import annotations

import json

import pandas as pd

from ar_pipeline.contracts import fingerprint
from ar_pipeline.engines.trade_audit import run_trade_audit
from ar_pipeline.manifest import RunContext


def run(ctx: RunContext) -> dict[str, str]:
    input_trades = ctx.stage_dir(4) / "quote_filled_trades.parquet"
    out = ctx.stage_dir(7)
    summary = run_trade_audit(input_trades, out)
    trades = pd.read_parquet(input_trades)
    statuses = trades.get("quote_fill_status", pd.Series("", index=trades.index)).astype(str)
    full_fills = bool(len(trades)) and statuses.eq("filled").all()
    promotable = bool(trades.get("quote_fill_promotable", pd.Series(False, index=trades.index)).astype(bool).all())
    proxy = bool(trades.get("quote_fill_mode", pd.Series("", index=trades.index)).astype(str).eq("source_proxy_test_only").any())
    robustness = ctx.stage_dir(6) / "robustness_report.md"
    gate = {
        "schema_version": 2,
        "config_fingerprint": fingerprint(ctx.config),
        "quote_trade_rows": int(len(trades)),
        "quote_fill_complete": full_fills,
        "quote_evidence_promotable": promotable,
        "source_proxy_detected": proxy,
        "robustness_review_present": robustness.exists(),
        "trade_audit": summary,
        "oos_eligible_for_human_review": bool(full_fills and promotable and not proxy and robustness.exists()),
        "oos_approved": False,
        "next_action": "explicit human approval required; sealed holdout must remain unread until then",
    }
    out.mkdir(parents=True, exist_ok=True)
    gate_path = out / "oos_gate.json"
    gate_path.write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verdict = ctx.notes_dir / "s07_trade_audit_and_oos_gate_verdict.md"
    ctx.notes_dir.mkdir(parents=True, exist_ok=True)
    if not verdict.exists():
        verdict.write_text(
            """# Stage 7 Trade Audit and OOS Gate Verdict

## Duplicate / Overlap Verdict

## Timestamp Semantics Verdict

## Symbol / Regime Concentration Verdict

## Source vs Quote Verdict

## Robustness / Multiple-Testing Verdict

## OOS Approval Decision (sealed holdout remains locked)

""",
            encoding="utf-8",
        )
    return {
        "duplicate_trades": str(out / "duplicate_trades.csv"),
        "same_timestamp": str(out / "same_timestamp_multiple_trades.csv"),
        "concentration": str(out / "symbol_concentration_top5.csv"),
        "report": str(out / "trade_audit_report.md"),
        "oos_gate": str(gate_path),
        "agent_verdict": str(verdict),
    }
