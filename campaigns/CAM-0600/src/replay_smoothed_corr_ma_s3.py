from __future__ import annotations

import argparse

import replay_smoothed_corr_ma as base
from run_smoothed_corr_ma import build
from suite_core import CAMPAIGNS, load_panels

base.OUT = CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0040"
base.RUN = CAMPAIGNS / "CAM-0600" / "runs" / "RUN-0040.yaml"
base.PRIOR = [base.OUT, CAMPAIGNS / "CAM-0600" / "artifacts" / "RUN-0039", *base.PRIOR]


def smoothing3_weights():
    panel = load_panels()["sp500"]
    return panel, build(panel, 0.8, 3, None)


base.weights = smoothing3_weights


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["ledgers", "missing", "replay"])
    args = parser.parse_args()
    {"ledgers": base.ledgers, "missing": base.missing, "replay": base.replay}[args.phase]()
