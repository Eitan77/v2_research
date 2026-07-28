from __future__ import annotations

from ar_pipeline.manifest import RunContext


def run(ctx: RunContext) -> dict[str, str]:
    ctx.notes_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.notes_dir / "s01_idea_pack.md"
    if not path.exists():
        path.write_text(
            """# Stage 1 Idea Pack

## Thesis

## Timing Rules

## Feature Families

## Scan Space

## Known Failure Modes To Watch

## Promotion Criteria

## OOS Holdout Lock

""",
            encoding="utf-8",
        )
    return {"idea_pack": str(path)}
