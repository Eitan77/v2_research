from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STAGE_NAMES = {
    0: "data_preflight",
    1: "idea_pack",
    2: "discovery",
    3: "promotion_review",
    4: "quote_fill",
    5: "post_fill_review",
    6: "robustness_validation",
    7: "trade_audit_and_oos_gate",
    8: "oos",
    9: "paper_package",
}


@dataclass
class RunContext:
    run_path: Path
    manifest: dict[str, Any]
    config: dict[str, Any]

    @property
    def notes_dir(self) -> Path:
        return self.run_path / "notes"

    def stage_dir(self, stage: int) -> Path:
        return self.run_path / f"stage_{stage:02d}_{STAGE_NAMES[stage]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_manifest(run_path: Path) -> dict[str, Any]:
    path = run_path / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing run manifest: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(run_path: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = utc_now()
    (run_path / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def mark_stage(run_path: Path, manifest: dict[str, Any], stage: int, status: str, outputs: dict[str, str] | None = None) -> None:
    stages = manifest.setdefault("stages", {})
    stages[str(stage)] = {
        "name": STAGE_NAMES[stage],
        "status": status,
        "updated_at": utc_now(),
        "outputs": outputs or {},
    }
    save_manifest(run_path, manifest)
