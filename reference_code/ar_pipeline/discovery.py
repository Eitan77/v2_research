from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from .execution import WorkloadInfo, resolve_execution


ENGINE_MODULES = {
    "cross_sectional_rank": "ar_pipeline.strategies.cross_sectional_rank",
    "event_reversal": "ar_pipeline.strategies.event_reversal",
    "exhaustive_intraday_long": "ar_pipeline.strategies.exhaustive_intraday_long",
    "leveraged_etf_intraday": "ar_pipeline.strategies.leveraged_etf_intraday",
    "soxl_soxs_pair_intraday": "ar_pipeline.strategies.soxl_soxs_pair_intraday",
}


def run_discovery(config: dict[str, Any], output_dir: Path) -> dict[str, str]:
    scan = config.get("scan", {})
    engine = scan.get("engine", "cross_sectional_rank")
    module_name = ENGINE_MODULES.get(engine)
    if module_name is None:
        raise ValueError(
            f"Unknown scan.engine {engine!r}. Add a strategy adapter under "
            "src/ar_pipeline/strategies and register it in ar_pipeline.discovery."
        )
    module = importlib.import_module(module_name)
    workload = module.estimate_workload(config) if hasattr(module, "estimate_workload") else WorkloadInfo(
        pattern="unknown",
        preferred_device="cpu",
        supports_cuda=False,
        supports_cpu=True,
        supports_batch_autotune=False,
    )
    resolve_execution(config, workload, output_dir)
    return module.run(config, output_dir)


