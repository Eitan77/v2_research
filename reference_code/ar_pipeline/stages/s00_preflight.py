from __future__ import annotations

import json

from ar_pipeline.data import validate_catalog
from ar_pipeline.manifest import RunContext
from ar_pipeline.validation import SafetyGateError, assert_safe_run_config


def run(ctx: RunContext) -> dict[str, str]:
    """Freeze safety/config/data evidence before any strategy computation."""

    result = assert_safe_run_config(ctx.config)
    data = ctx.config["data"]
    report = validate_catalog(data["catalog_path"], full=False)
    timeframe = str(ctx.config["scan"]["timeframe"])
    derived_table = f"derived_bars_{timeframe}" if timeframe not in {"1m", "1d"} else None
    if data.get("require_session_aligned_bars", True) and derived_table:
        lineage = report.get("lineage", {}).get(derived_table, {})
        if not lineage.get("session_aligned_contract", False):
            raise SafetyGateError(
                f"{derived_table} has not been rebuilt under the session-aligned bar contract. "
                "Do not screen it; rebuild bars, features, labels, and the matrix first."
            )
    out = ctx.stage_dir(0)
    out.mkdir(parents=True, exist_ok=True)
    payload = {"run_config": result.to_dict(), "catalog_validation": report}
    path = out / "data_preflight.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown = out / "data_preflight.md"
    markdown.write_text(
        "\n".join(
            [
                "# Stage 0 Data Preflight",
                "",
                f"Config fingerprint: `{result.config_fingerprint}`",
                f"Catalog warnings: {len(report.get('warnings', []))}",
                "",
                "This artifact establishes timing, feed, adjustment, universe, and data-lineage constraints before a hypothesis is scanned.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {"preflight": str(path), "report": str(markdown)}
