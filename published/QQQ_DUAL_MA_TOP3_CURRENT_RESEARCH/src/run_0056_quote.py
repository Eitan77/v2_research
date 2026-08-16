from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))

import run_0054_quote as shared
from run_0033_exit_overlays import base_context
from run_0056_profit_ladders import ladder_weights, variant_specs

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0056"
shared.OUT = OUT
shared.IDS = (
    "control",
    "orig20_every10_to50",
    "orig10_every5_to25",
    "orig20_every5_to25",
    "orig25_every5_to20",
)


def weights():
    p, _, _, sig, base, _, _ = base_context()
    specs = variant_specs()
    return p, {name: ladder_weights(p, sig, base, specs[name])[0] for name in shared.IDS}


shared.weights = weights


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("ledgers", "missing", "replay"))
    args = parser.parse_args()
    getattr(shared, args.phase)()
