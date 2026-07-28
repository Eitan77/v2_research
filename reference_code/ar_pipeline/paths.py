from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path("D:/AlgoResearch")
DATA_ROOT = PROJECT_ROOT / "data"
PIPELINE_ROOT = PROJECT_ROOT / "research_pipeline"
RUNS_ROOT = PIPELINE_ROOT / "runs"
PLAYBOOK_ROOT = PIPELINE_ROOT / "agent_playbooks"


def ensure_pipeline_dirs() -> None:
    for path in [PIPELINE_ROOT, RUNS_ROOT, PLAYBOOK_ROOT]:
        path.mkdir(parents=True, exist_ok=True)
