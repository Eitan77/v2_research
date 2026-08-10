import argparse
import replay_daily_ma_quotes as base
from run_correlation_capped_ma import build
from suite_core import CAMPAIGNS, load_panels

base.OUT=CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0029"
base.RUN=CAMPAIGNS/"CAM-0610"/"runs"/"RUN-0029.yaml"
base.VARIANT="sp500__ma200__daily__top10__corr0.8"
base.EXTRA_QUOTE_DIRS=[CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0025",CAMPAIGNS/"CAM-0610"/"artifacts"/"RUN-0027"]
base.BAR_RUN="RUN-0028"
base.weights_and_panel=lambda: ((lambda p:(p,build(p,.8)))(load_panels()["sp500"]))

if __name__=="__main__":
 p=argparse.ArgumentParser(); p.add_argument("phase",choices=["ledgers","replay"]); a=p.parse_args(); base.ledgers() if a.phase=="ledgers" else base.replay()
