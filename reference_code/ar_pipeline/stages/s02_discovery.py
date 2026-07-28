from __future__ import annotations

from ar_pipeline.discovery import run_discovery
from ar_pipeline.manifest import RunContext


def run(ctx: RunContext) -> dict[str, str]:
    out = ctx.stage_dir(2)
    return run_discovery(ctx.config, out)
