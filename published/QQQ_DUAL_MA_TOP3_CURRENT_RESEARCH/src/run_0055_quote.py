from __future__ import annotations

import argparse
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))

import run_0054_quote as shared
from run_0033_exit_overlays import base_context
from run_0054_profit_trims import build_trim_weights

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0055"
shared.OUT = OUT
shared.IDS = ("control", "weekly_t20_f75", "weekly_t25_f75")


def weights():
    p, _, _, sig, base, _, _ = base_context()
    variants = {
        "control": None,
        "weekly_t20_f75": {"mode": "weekly", "thresholds": [0.20], "fractions": [0.75]},
        "weekly_t25_f75": {"mode": "weekly", "thresholds": [0.25], "fractions": [0.75]},
    }
    return p, {name: build_trim_weights(p, sig, base, spec)[0] for name, spec in variants.items()}


shared.weights = weights


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("ledgers", "missing", "replay"))
    args = parser.parse_args()
    getattr(shared, args.phase)()
