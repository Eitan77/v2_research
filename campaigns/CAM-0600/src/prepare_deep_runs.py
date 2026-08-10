from __future__ import annotations

from pathlib import Path

import yaml

from suite_core import CAMPAIGNS


CAMPAIGN_IDS = tuple(f"CAM-{i:04d}" for i in range(600, 625))


def main() -> None:
    for campaign_id in CAMPAIGN_IDS:
        path = CAMPAIGNS / campaign_id / "runs" / "RUN-0008.yaml"
        if path.exists():
            raise RuntimeError(f"refusing to overwrite {path}")
        payload = {
            "run_id": "RUN-0008",
            "campaign_id": campaign_id,
            "parent_run": "RUN-0003",
            "status": "planned",
            "change": "Deeper mechanism-driven S&P-first development grid with QQQ/ETF comparisons and target-change execution correction.",
            "reason": "User requested continued diagnosis and economically motivated adaptations for every source strategy.",
            "expected_effect": "Identify whether causal universe, breadth, confirmation, risk-state, or portfolio construction repairs alpha and consistency without retrospective ticker selection.",
            "frozen_contract": "campaigns/CAM-0600/DEEP_DEVELOPMENT_CONTRACT.yaml",
            "configuration": {
                "discovery_cutoff": "2026-04-30",
                "holdout_access": False,
                "fixed_base": 1.0,
                "broker_margin": False,
                "costs_bps_per_side": [-1, 0, 1, 2, 5, 10],
                "recent_windows_months": [12, 15, 18],
                "primary_universe": "point_in_time_sp500",
                "comparison_universes": ["point_in_time_qqq", "declared_etfs"],
            },
            "result": None,
            "decision": None,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    print(f"froze {len(CAMPAIGN_IDS)} RUN-0008 records")


if __name__ == "__main__":
    main()
