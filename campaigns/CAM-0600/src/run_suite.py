from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from baseline_strategies import build_baselines, build_fundamentals_for_panels
from fundamentals import FundamentalMatrices
from suite_core import (
    CAMPAIGNS,
    COSTS_BPS,
    CUTOFF,
    HOLDOUT,
    evaluate_weights,
    jsonable,
    load_panels,
    save_variant,
    semantic_fixtures,
    write_json,
)


CAMPAIGN_IDS = tuple(f"CAM-{i:04d}" for i in range(600, 625))
FUNDAMENTAL_CAMPAIGNS = {
    "CAM-0602", "CAM-0604", "CAM-0605", "CAM-0609", "CAM-0623", "CAM-0624"
}
SHARED = CAMPAIGNS / "CAM-0600" / "artifacts" / "shared"


def _fundamental_cache_paths(name: str) -> tuple[Path, Path]:
    return SHARED / f"fundamental_matrices_{name}.npz", SHARED / f"fundamental_coverage_{name}.json"


def _load_or_build_fundamentals(
    panels: dict[str, Any],
) -> tuple[dict[str, FundamentalMatrices], dict[str, Any]]:
    arrays: dict[str, FundamentalMatrices] = {}
    coverage: dict[str, Any] = {}
    complete = True
    for name in ("sp500", "qqq"):
        npz_path, json_path = _fundamental_cache_paths(name)
        if not npz_path.exists() or not json_path.exists():
            complete = False
            break
    if complete:
        for name in ("sp500", "qqq"):
            npz_path, json_path = _fundamental_cache_paths(name)
            data = np.load(npz_path)
            report = json.loads(json_path.read_text(encoding="utf-8"))
            expected_shape = panels[name].adj_close.shape
            if tuple(data["book_to_price"].shape) != expected_shape:
                complete = False
                break
            arrays[name] = FundamentalMatrices(
                book_to_price=data["book_to_price"],
                market_cap=data["market_cap"],
                chs_logit=data["chs_logit"],
                profitability=data["profitability"],
                leverage=data["leverage"],
                cash_ratio=data["cash_ratio"],
                coverage=report,
            )
            coverage[name] = report
    if complete:
        return arrays, coverage

    arrays, coverage = build_fundamentals_for_panels(panels)
    SHARED.mkdir(parents=True, exist_ok=True)
    for name, matrix in arrays.items():
        npz_path, json_path = _fundamental_cache_paths(name)
        np.savez_compressed(
            npz_path,
            book_to_price=matrix.book_to_price,
            market_cap=matrix.market_cap,
            chs_logit=matrix.chs_logit,
            profitability=matrix.profitability,
            leverage=matrix.leverage,
            cash_ratio=matrix.cash_ratio,
        )
        write_json(json_path, coverage[name])
    return arrays, coverage


def _preflight(panels: dict[str, Any]) -> dict[str, Any]:
    report: dict[str, Any] = {"semantic_fixtures": semantic_fixtures(), "panels": {}}
    for name, panel in panels.items():
        maximum = pd.Timestamp(panel.dates.max())
        minimum = pd.Timestamp(panel.dates.min())
        if maximum > CUTOFF or (panel.dates >= HOLDOUT).any():
            raise RuntimeError(f"{name} crossed the sealed holdout boundary")
        report["panels"][name] = {
            "minimum_date": str(minimum.date()),
            "maximum_date": str(maximum.date()),
            "dates": int(panel.n_dates),
            "symbols": int(panel.n_symbols),
            "member_cells": int(panel.member.sum()),
            "holdout_rows_loaded": 0,
        }
    return report


def run_campaign(
    campaign_id: str,
    panels: dict[str, Any],
    fundamental: dict[str, FundamentalMatrices],
    preflight: dict[str, Any],
) -> dict[str, Any]:
    output = CAMPAIGNS / campaign_id / "artifacts" / "RUN-0001"
    variants = build_baselines(campaign_id, panels, fundamental)
    records: list[dict[str, Any]] = []
    detail_count = 0
    for variant in variants:
        for cost in COSTS_BPS:
            metrics, daily, monthly, yearly, symbols = evaluate_weights(
                variant.panel,
                variant.weights,
                cost,
                holding=variant.holding,
                execution_lag=variant.execution_lag,
                return_override=variant.return_override,
            )
            record = {
                "campaign_id": campaign_id,
                "variant_id": variant.variant_id,
                **metrics,
                "holding": variant.holding,
                "metadata_json": json.dumps(jsonable(variant.metadata), sort_keys=True),
            }
            records.append(record)
            save_detail = float(cost) == 2.0
            if save_detail:
                detail_count += 1
            save_variant(
                output,
                f"{variant.variant_id}__cost_{cost:g}bps",
                record,
                daily,
                monthly,
                yearly,
                symbols,
                save_detail=save_detail,
            )
    metrics_df = pd.DataFrame(records)
    metrics_df = metrics_df.sort_values(
        ["cost_bps_per_side", "net_simple_return"], ascending=[True, False]
    )
    output.mkdir(parents=True, exist_ok=True)
    metrics_df.to_csv(output / "variant_metrics.csv", index=False)
    at_two = metrics_df[metrics_df["cost_bps_per_side"] == 2.0].sort_values(
        "net_simple_return", ascending=False
    )
    best = at_two.iloc[0].to_dict() if len(at_two) else None
    report = {
        "campaign_id": campaign_id,
        "run_id": "RUN-0001",
        "status": "completed",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "logical_cpu_count": __import__("os").cpu_count(),
        "source_variant_count": int(len(variants)),
        "executed_variant_cost_count": int(len(records)),
        "detail_variant_count_at_2bps": int(detail_count),
        "costs_bps_per_side": list(COSTS_BPS),
        "best_at_2bps": jsonable(best),
        "preflight": preflight,
        "maximum_loaded_date": str(CUTOFF.date()),
        "holdout_rows_loaded": 0,
        "fixed_base": 1.0,
        "compounding": False,
        "margin": "none",
        "interpretation_blockers": [],
    }
    write_json(output / "execution_report.json", report)
    print(
        f"{campaign_id}: {len(variants)} source variants, {len(records)} variant-costs; "
        f"best 2bps={best['variant_id'] if best else 'none'} "
        f"net={best['net_simple_return'] if best else 'n/a'}",
        flush=True,
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--campaign", choices=CAMPAIGN_IDS)
    target.add_argument("--all", action="store_true")
    parser.add_argument("--start", choices=CAMPAIGN_IDS)
    args = parser.parse_args()

    selected = CAMPAIGN_IDS if args.all else (args.campaign,)
    if args.start:
        if not args.all:
            parser.error("--start requires --all")
        selected = tuple(x for x in selected if x >= args.start)
    panels = load_panels()
    preflight = _preflight(panels)
    fundamental: dict[str, FundamentalMatrices] = {}
    fundamental_coverage: dict[str, Any] = {}
    if FUNDAMENTAL_CAMPAIGNS.intersection(selected):
        fundamental, fundamental_coverage = _load_or_build_fundamentals(panels)
        preflight["fundamental_coverage"] = fundamental_coverage
    for campaign_id in selected:
        run_campaign(campaign_id, panels, fundamental, preflight)


if __name__ == "__main__":
    main()
