"""Wait for a Terra discovery ledger, then run the capital hard gate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--poll-seconds", type=int, default=30)
    ap.add_argument("--timeout-hours", type=float, default=12.0)
    args = ap.parse_args()
    stage = args.run / "stage_02_discovery"
    trades = stage / "discovery_trades.parquet"
    deadline = time.time() + args.timeout_hours * 3600.0
    while not trades.exists():
        if time.time() >= deadline:
            raise TimeoutError(f"timed out waiting for {trades}")
        if (stage / "error.txt").exists():
            raise RuntimeError((stage / "error.txt").read_text(encoding="utf-8"))
        time.sleep(max(1, args.poll_seconds))
    out = stage / "terra_hard_gate"
    cmd = [
        sys.executable,
        str(Path(__file__).with_name("terra_hard_gate.py")),
        "--trades", str(trades),
        "--catalog", "D:/AlgoResearch/data/catalog.duckdb",
        "--out", str(out),
        "--costs", "0", "2", "5", "10", "25", "50",
        "--target-monthly-pct", "10",
    ]
    result = subprocess.run(cmd, check=False)
    status = {"trades": str(trades), "validator_exit_code": result.returncode, "output": str(out)}
    (stage / "post_discovery_status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
