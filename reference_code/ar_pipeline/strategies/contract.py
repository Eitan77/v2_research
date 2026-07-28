from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ar_pipeline.execution import WorkloadInfo


class StrategyAdapter(Protocol):
    """Discovery adapter contract.

    A strategy adapter owns candidate/trade generation for one strategy family.
    The rest of the pipeline owns slippage review, promotion artifacts,
    quote-fill, trade audit, OOS gating, and reporting.
    """

    def estimate_workload(self, config: dict[str, Any]) -> WorkloadInfo:
        """Describe compute pattern before the scan runs."""

    def run(self, config: dict[str, Any], output_dir: Path) -> dict[str, str]:
        """Write standard discovery artifacts and return their paths."""
