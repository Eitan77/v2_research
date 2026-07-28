from __future__ import annotations

import argparse
import json
from pathlib import Path

from alpaca_research.catalog import init_catalog
from alpaca_research.research import build_forward_labels, build_research_matrix


def main() -> None:
    parser = argparse.ArgumentParser(description="Build repeatable causal labels and research matrices for Terra.")
    parser.add_argument("--root", default="D:/AlgoResearch")
    parser.add_argument("--timeframes", default="5m,10m,15m,30m,1h,4h")
    parser.add_argument("--start", default="2019-06-21")
    parser.add_argument("--end", default="2026-05-31")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--skip-labels", action="store_true", help="Resume after labels already exist; refresh the catalog before matrix joins.")
    args = parser.parse_args()
    project_root = Path(args.root).resolve()
    root = project_root / "data"
    timeframes = [x.strip() for x in args.timeframes.split(",") if x.strip()]
    horizons = {
        "5m": [1, 2, 3, 6, 12, 24, 48],
        "10m": [1, 2, 3, 6, 12, 24],
        "15m": [1, 2, 3, 4, 6, 12, 24],
        "30m": [1, 2, 3, 4, 6, 12],
        "1h": [1, 2, 3, 4, 6],
        "4h": [1, 2, 3, 4],
    }
    results = {}
    init_catalog(root)
    for timeframe in timeframes:
        results[timeframe] = {}
        if not args.skip_labels:
            results[timeframe]["labels"] = build_forward_labels(root, timeframe, None, horizons[timeframe], args.start, args.end, source="derived", workers=args.workers, overwrite=args.overwrite)
            # The catalog is file-backed; expose the just-written label
            # partition before the matrix workers open their own connections.
            init_catalog(root)
        results[timeframe]["matrix"] = build_research_matrix(root, timeframe, None, overwrite=args.overwrite, workers=args.workers)
    results["catalog"] = str(init_catalog(root))
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
