from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
CUTOFF = date(2026, 4, 30)
CAMPAIGNS = [ROOT / "campaigns" / "CAM-0631", ROOT / "campaigns" / "CAM-0632"]


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader: UniqueLoader, node: yaml.nodes.MappingNode, deep: bool = False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def walk_json(value, stats: dict) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "holdout_rows_loaded":
                stats["holdout_assertions"] += 1
                if child != 0:
                    raise ValueError(f"nonzero holdout_rows_loaded: {child}")
            if key in {"maximum_loaded_date", "maximum_loaded_session"} and child:
                stats["cutoff_assertions"] += 1
                parsed = date.fromisoformat(str(child)[:10])
                if parsed > CUTOFF:
                    raise ValueError(f"post-cutoff maximum loaded date: {child}")
            walk_json(child, stats)
    elif isinstance(value, list):
        for child in value:
            walk_json(child, stats)


def audit_campaign(campaign: Path) -> dict:
    stats = {
        "yaml_files": 0,
        "run_files": 0,
        "json_reports": 0,
        "jsonl_lines": 0,
        "python_files": 0,
        "artifact_files": 0,
        "holdout_assertions": 0,
        "cutoff_assertions": 0,
    }
    yaml_documents = {}
    for path in [campaign / "PLAN.yaml", campaign / "RESULTS.yaml", *sorted((campaign / "runs").glob("RUN-*.yaml"))]:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
        yaml_documents[path.name] = document
        stats["yaml_files"] += 1
        if path.parent.name == "runs":
            stats["run_files"] += 1
            if document.get("run_id") != path.stem:
                raise ValueError(f"run id mismatch: {path}")
            if document.get("status") == "frozen_pre_execution":
                raise ValueError(f"unfinished run record: {path}")
            if not (campaign / "artifacts" / path.stem).exists():
                raise ValueError(f"missing artifact directory: {path.stem}")
    checklist = (campaign / "RULE_CHECKLIST.md").read_text(encoding="utf-8")
    if "- [ ]" in checklist:
        raise ValueError("unchecked conclusion item")
    events = {}
    for line in (campaign / "WORKLOG.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        stats["jsonl_lines"] += 1
        events.setdefault(record["run_id"], set()).add(record["event"])
    for run_path in sorted((campaign / "runs").glob("RUN-*.yaml")):
        run_id = run_path.stem
        if "frozen" not in events.get(run_id, set()) or not ({"completed", "failed"} & events.get(run_id, set())):
            raise ValueError(f"incomplete worklog lifecycle: {run_id}")
    for path in sorted((campaign / "artifacts").rglob("*.json")):
        if "CONCLUSION_AUDIT" in path.parts:
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        stats["json_reports"] += 1
        walk_json(value, stats)
    for path in sorted((campaign / "artifacts").rglob("*")):
        if path.is_file() and "CONCLUSION_AUDIT" not in path.parts:
            stats["artifact_files"] += 1
            if path.stat().st_size == 0:
                raise ValueError(f"empty artifact: {path}")
    for path in sorted((campaign / "src").glob("*.py")):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
        stats["python_files"] += 1
    source_hash = None
    source = yaml_documents["PLAN.yaml"].get("source")
    if source:
        source_path = Path(source)
        if not source_path.exists():
            raise ValueError(f"missing declared source: {source_path}")
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return {
        "campaign": campaign.name,
        "status": "passed",
        "results_status": yaml_documents["RESULTS.yaml"].get("status"),
        "counts": stats,
        "declared_source_sha256": source_hash,
        "cutoff": CUTOFF.isoformat(),
    }


def main() -> None:
    reports = []
    for campaign in CAMPAIGNS:
        report = audit_campaign(campaign)
        out = campaign / "artifacts" / "CONCLUSION_AUDIT"
        out.mkdir(parents=True, exist_ok=True)
        payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **report}
        (out / "integrity_report.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        reports.append(payload)
    print(json.dumps({"status": "passed", "campaigns": reports}, indent=2))


if __name__ == "__main__":
    main()
