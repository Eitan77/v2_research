from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))

import run_0054_quote as shared
from run_0033_exit_overlays import base_context
from run_0056_profit_ladders import ladder_weights
from run_0057_micro_ladders import variant_specs

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0057"
shared.OUT = OUT
shared.IDS = (
    "control",
    "orig5_every1_to20_exit100",
    "rem5_every1_to20_leave35p8",
    "orig5_every2_to20_leave50",
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
