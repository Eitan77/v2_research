from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[3]


def git_show(path: str) -> str:
    return subprocess.check_output(["git", "show", f"HEAD:{path}"], cwd=WORKSPACE, text=True, encoding="utf-8")


def stage_blob(path: str, content: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    try:
        blob = subprocess.check_output(["git", "hash-object", "-w", str(temporary)], cwd=WORKSPACE, text=True).strip()
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"], cwd=WORKSPACE, check=True)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    current_path = "research/CURRENT.md"
    current_head = git_show(current_path)
    current_work = (WORKSPACE / current_path).read_text(encoding="utf-8")
    checkpoint_start = current_work.index("## 2026-08-06 checkpoint")
    checkpoint_end = current_work.index("\n> This file states", checkpoint_start)
    checkpoint = current_work[checkpoint_start:checkpoint_end].rstrip() + "\n\n"
    title, rest = current_head.split("\n", 1)
    stage_blob(current_path, title + "\n\n" + checkpoint + rest)

    knowledge_path = "research/KNOWLEDGE.md"
    knowledge_head = git_show(knowledge_path).rstrip() + "\n\n"
    knowledge_work = (WORKSPACE / knowledge_path).read_text(encoding="utf-8")
    marker = "## 2026-08-06 — SSRN 151-strategy equity and ETF series"
    addition = knowledge_work[knowledge_work.index(marker):].rstrip() + "\n"
    stage_blob(knowledge_path, knowledge_head + addition)

    ledger_path = "research/LEDGER.md"
    ledger_head = git_show(ledger_path)
    ledger_work = (WORKSPACE / ledger_path).read_text(encoding="utf-8")
    campaign_lines = [
        line for line in ledger_work.splitlines()
        if any(line.startswith(f"| CAM-{number:04d} |") for number in range(600, 625))
    ]
    if len(campaign_lines) != 25:
        raise RuntimeError(f"expected 25 campaign ledger lines, got {len(campaign_lines)}")
    insertion = "\n".join(campaign_lines) + "\n\n"
    stage_blob(ledger_path, ledger_head.replace("## New campaign row", insertion + "## New campaign row", 1))
    print("staged selective CURRENT, KNOWLEDGE, and LEDGER additions")


if __name__ == "__main__":
    main()
