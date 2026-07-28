from __future__ import annotations

import argparse
import json
from pathlib import Path

from ar_pipeline.config import read_structured, write_structured
from ar_pipeline.manifest import save_manifest, utc_now
from ar_pipeline.runner import create_run, run_stage, run_through


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or resume a repeatable Terra long-only research run.")
    parser.add_argument("--config", default="D:/AlgoResearch/research_pipeline/configs/terra_long_only_gpu_rank_20260709.json")
    parser.add_argument("--through-stage", type=int, default=2, choices=range(0, 8))
    parser.add_argument("--run", default="", help="Existing run directory to resume.")
    parser.add_argument("--name", default="", help="Override run name when creating a run.")
    parser.add_argument("--timeframe", default="", help="Optional timeframe override for a GPU rank run.")
    parser.add_argument("--holding-bars", type=int, default=0, help="Optional holding-bar override.")
    parser.add_argument("--formulas", type=int, default=0, help="Optional formula-count override.")
    parser.add_argument("--workers", type=int, default=16, help="CPU/quote-fill worker count for supported stages (default: 16).")
    args = parser.parse_args()

    source = Path(args.config).resolve()
    config = read_structured(source)
    if args.timeframe:
        config.setdefault("scan", {})["timeframe"] = args.timeframe
        config.setdefault("run_name", config.get("name", "terra_long_only"))
        config["run_name"] = f"{config['run_name']}_{args.timeframe}"
        config["name"] = config["run_name"]
    if args.holding_bars:
        config.setdefault("scan", {})["holding_bars"] = args.holding_bars
    if args.formulas:
        config.setdefault("scan", {})["formulas"] = args.formulas
    if args.workers < 1:
        raise ValueError("--workers must be >= 1")
    config.setdefault("scan", {}).setdefault("execution", {})["workers"] = args.workers
    config.setdefault("quote_fill", {})["workers"] = args.workers

    if args.run:
        run_path = Path(args.run).resolve()
        if not (run_path / "scan.yaml").exists():
            raise FileNotFoundError(run_path / "scan.yaml")
        write_structured(run_path / "scan.yaml", config)
        outputs = run_through(run_path, args.through_stage)
    else:
        name = args.name or str(config.get("run_name") or config.get("name") or "terra_long_only")
        run_path = create_run(name, template="intraday_rank_long_only")
        write_structured(run_path / "scan.yaml", config)
        manifest_path = run_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["template"] = "terra_long_only_repeatable"
        manifest["source_config"] = str(source)
        manifest["holdout_locked"] = True
        manifest["created_from_config_at"] = utc_now()
        save_manifest(run_path, manifest)
        outputs = run_through(run_path, args.through_stage)

    print(json.dumps({"run_path": str(run_path), "through_stage": args.through_stage, "outputs": outputs}, indent=2))


if __name__ == "__main__":
    main()
