from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "campaigns" / "CAM-0611" / "src"
sys.path.insert(0, str(SRC))

import run_0063_profit_lock_tiers as campaign

campaign.OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0064"
campaign.SPECS = [(tier, beta) for tier in (1.5, 2.0, 2.5) for beta in (0.25, 0.5)]


if __name__ == "__main__":
    campaign.main()
