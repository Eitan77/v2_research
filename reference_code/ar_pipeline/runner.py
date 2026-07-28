from __future__ import annotations

import importlib
import shutil
from pathlib import Path
from typing import Any

from .config import read_structured, write_structured
from .manifest import RunContext, STAGE_NAMES, load_manifest, mark_stage, save_manifest, utc_now
from .paths import RUNS_ROOT, ensure_pipeline_dirs
from .validation import SafetyGateError


STAGE_MODULES = {
    0: "ar_pipeline.stages.s00_preflight",
    1: "ar_pipeline.stages.s01_idea_pack",
    2: "ar_pipeline.stages.s02_discovery",
    3: "ar_pipeline.stages.s03_promote",
    4: "ar_pipeline.stages.s04_quote_fill",
    5: "ar_pipeline.stages.s05_review",
    6: "ar_pipeline.stages.s06_variations",
    7: "ar_pipeline.stages.s07_trade_audit",
}


def create_run(name: str, template: str = "bar_screen_v2") -> Path:
    ensure_pipeline_dirs()
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name.lower()).strip("_")
    run_path = RUNS_ROOT / f"{utc_now()[:10].replace('-', '')}_{slug}"
    if run_path.exists():
        raise FileExistsError(run_path)
    (run_path / "notes").mkdir(parents=True)
    config = default_scan_config(template)
    write_structured(run_path / "scan.yaml", config)
    manifest = {
        "name": name,
        "template": template,
        "schema_version": 2,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "stages": {},
        "holdout_locked": True,
        "oos_requires_user_approval": True,
    }
    save_manifest(run_path, manifest)
    return run_path


def default_scan_config(template: str) -> dict[str, Any]:
    if template not in {"bar_screen_v2", "intraday_rank_long_only"}:
        raise ValueError(f"Unknown template: {template}")
    return {
        "schema_version": 2,
        "research": {
            "purpose": "hypothesis_screening_only",
            "sealed_holdout": {"start": "2026-01-01", "end": "2026-12-31", "locked": True},
            "trial_ledger_required": True,
        },
        "data": {
            "catalog_path": "D:/AlgoResearch/data/catalog.duckdb",
            "table": "research_matrix",
            "feed": "sip",
            "adjustment": "raw",
            # Alpaca minute bars are start-labelled.  This must be explicit
            # for each vendor/timeframe rather than inferred by an adapter.
            "bar_timestamp_label": "start",
            "require_session_aligned_bars": True,
            "universe": {"mode": "all"},
        },
        "scan": {
            "engine": "cross_sectional_rank",
            "family": "unclassified",
            "timeframe": "15m",
            "session": "rth",
            "train_start": "2020-01-01",
            "train_end": "2025-12-31",
            "holding_bars": 4,
            "entry_model": "next_actionable_bar_open",
            "decision_latency_ms": 250,
            "features": [
                "close_vs_sma_20",
                "close_vs_ema_20",
                "bb_percent_b_20_2",
                "atr_pct_14",
                "rsi_14",
                "macd_hist_12_26_9",
                "adx_14",
                "relative_volume_20",
                "hl_range_pct",
                "body_pct",
            ],
            "formulas": 512,
            "top_ns": [1, 2, 3],
            "cost_bps_per_side_grid": [0.0, 2.0, 5.0, 10.0, 25.0, 50.0],
            "execution": {
                "device": "auto",
                "workers": "auto",
                "batch_size": "auto",
                "benchmark": True,
                "fail_if_cpu_fallback": True,
                "require_accelerated": True,
            },
            "seed": 20260629,
        },
        "promotion": {
            "min_trades": 100,
            "min_cagr": 0.0,
            "max_drawdown": -0.35,
            "review_top": 50,
            "min_active_days": 25,
            "min_portfolio_simple_return": 0.0,
            "min_return_on_deployed_capital": 0.0,
            "portfolio_prefilter_multiplier": 20,
        },
        "bar_fill": {
            "slippage_bps_per_side": 2.0,
            "fee_bps_per_side": 0.0,
            "participation_rate": 0.05,
            "intrabar_ambiguity": "worst_case",
        },
        "quote_fill": {
            "mode": "alpaca_sip_quote_path",
            "feed": "sip",
            "order_latency_ms": 250,
            "max_quote_wait_ms": 2000,
            "displayed_size_participation": 0.05,
            "allow_partial_fills": False,
            "require_full_path_for_brackets": True,
            "workers": "auto",
        },
        "post_fill": {
            "min_quote_total": 0.0,
            "min_quote_portfolio_simple_return": 0.0,
            "min_quote_return_on_deployed_capital": 0.0,
            "min_active_days": 25,
            "max_avg_source_quote_gap_abs": 0.05,
            "min_fill_rate": 1.0,
        },
        "portfolio": {
            "capital_model": "one_position_at_a_time",
            "allocation_per_trade": 1.0,
            "overlap_policy": "skip_while_flat",
            "raw_signal_compounding_promotable": False,
        },
        "variations": {"formulas": 512, "review_top": 30},
    }


def load_context(run_path: str | Path) -> RunContext:
    path = Path(run_path)
    manifest = load_manifest(path)
    config = read_structured(path / "scan.yaml")
    return RunContext(run_path=path, manifest=manifest, config=config)


def run_stage(run_path: str | Path, stage: int) -> dict[str, str]:
    if stage not in STAGE_MODULES:
        raise ValueError(f"Stage {stage} is not implemented for agent-operated run mode")
    ctx = load_context(run_path)
    if int(ctx.config.get("schema_version", 0) or 0) >= 2 and stage >= 1:
        preflight = ctx.manifest.get("stages", {}).get("0", {})
        if preflight.get("status") != "complete":
            raise SafetyGateError("Stage 0 data preflight must complete before any research stage")
    module = importlib.import_module(STAGE_MODULES[stage])
    outputs = module.run(ctx)
    mark_stage(ctx.run_path, ctx.manifest, stage, "complete", outputs)
    return outputs


def run_through(run_path: str | Path, through_stage: int) -> None:
    for stage in [0, *range(1, through_stage + 1)]:
        if stage > 7:
            raise ValueError("Stages 8-9 require explicit user approval and are not run by run-through.")
        if stage == 4:
            from .approvals import approved_quote_candidates

            try:
                approved_quote_candidates(run_path)
            except SafetyGateError as exc:
                raise SafetyGateError(
                    "run-through intentionally stops after Stage 3 until an explicit quote-fill approval is recorded. "
                    "Use `ar-pipeline approve-quote` with a written rationale, then resume."
                ) from exc
        run_stage(run_path, stage)


def install_playbooks(playbook_root: Path) -> None:
    ensure_pipeline_dirs()
    playbook_root.mkdir(parents=True, exist_ok=True)
    playbooks = {
        "s01_idea_pack.md": "Define thesis, completed-bar timing, forbidden data, source availability, search space, trial count, and locked OOS before discovery.\n",
        "s03_promotion_review.md": "Screening eligibility is not promotion. Approve quote work only with a written rationale, cost/portfolio evidence, and an exact canonical order-intent ledger.\n",
        "s05_post_fill_review.md": "Treat proxy evidence, missing/partial SIP fills, quote/source collapse, and quote-filled portfolio collapse as hard stops.\n",
        "s06_variation_design.md": "Use chronological walk-forward folds with outcome embargoes. Test nearby parameters, regimes, costs, timing, and execution rules; count every trial.\n",
        "s07_trade_audit_review.md": "Audit data provenance, timestamp availability, universe/PIT evidence, split/corporate-action risk, fills, concentration, capital overlap, and stop/target path semantics before OOS.\n",
    }
    for name, body in playbooks.items():
        path = playbook_root / name
        if not path.exists():
            path.write_text("# " + name.replace("_", " ").replace(".md", "").title() + "\n\n" + body, encoding="utf-8")
