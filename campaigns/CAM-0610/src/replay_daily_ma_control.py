import argparse
import replay_daily_ma_quotes as base
from suite_core import CAMPAIGNS

base.OUT = CAMPAIGNS / "CAM-0610" / "artifacts" / "RUN-0027"
base.RUN = CAMPAIGNS / "CAM-0610" / "runs" / "RUN-0027.yaml"
base.VARIANT = "sp500__ungated__daily__top10__momentum"
base.GATED = False
base.EXTRA_QUOTE_DIRS = [CAMPAIGNS / "CAM-0610" / "artifacts" / "RUN-0025"]

if __name__ == "__main__":
    p=argparse.ArgumentParser(); p.add_argument("phase",choices=["ledgers","replay"]); a=p.parse_args()
    base.ledgers() if a.phase=="ledgers" else base.replay()
