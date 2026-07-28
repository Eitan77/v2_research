from __future__ import annotations

import argparse
import json
from pathlib import Path

from .approvals import approve_quote_fill
from .data import validate_catalog
from .paths import PLAYBOOK_ROOT, ensure_pipeline_dirs
from .runner import create_run, install_playbooks, run_stage, run_through


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ar-pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    new = sub.add_parser("new-run")
    new.add_argument("--name", required=True)
    new.add_argument("--template", default="bar_screen_v2")
    stage = sub.add_parser("stage")
    stage.add_argument("stage", type=int)
    stage.add_argument("--run", required=True)
    run = sub.add_parser("run")
    run.add_argument("--run", required=True)
    run.add_argument("--through-stage", type=int, required=True)
    approve = sub.add_parser("approve-quote")
    approve.add_argument("--run", required=True)
    approve.add_argument("--candidates", required=True, help="Comma-separated Stage 3 base candidate IDs")
    approve.add_argument("--rationale", required=True)
    approve.add_argument("--reviewer", default="operator")
    val = sub.add_parser("validate-data")
    val.add_argument("--catalog", default="D:/AlgoResearch/data/catalog.duckdb")
    val.add_argument("--full", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "init":
        ensure_pipeline_dirs()
        install_playbooks(PLAYBOOK_ROOT)
        print(json.dumps({"pipeline_root": "D:/AlgoResearch/research_pipeline"}, indent=2))
        return 0
    if args.cmd == "new-run":
        path = create_run(args.name, args.template)
        install_playbooks(PLAYBOOK_ROOT)
        print(json.dumps({"run_path": str(path)}, indent=2))
        return 0
    if args.cmd == "stage":
        outputs = run_stage(Path(args.run), args.stage)
        print(json.dumps(outputs, indent=2))
        return 0
    if args.cmd == "run":
        run_through(Path(args.run), args.through_stage)
        print(json.dumps({"run_path": args.run, "through_stage": args.through_stage}, indent=2))
        return 0
    if args.cmd == "approve-quote":
        path = approve_quote_fill(
            Path(args.run),
            [value.strip() for value in args.candidates.split(",")],
            rationale=args.rationale,
            reviewer=args.reviewer,
        )
        print(json.dumps({"approval": str(path)}, indent=2))
        return 0
    if args.cmd == "validate-data":
        print(json.dumps(validate_catalog(args.catalog, full=args.full), indent=2))
        return 0
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
