import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0600"/"src")); sys.path.insert(0,str(ROOT/"campaigns"/"CAM-0617"/"src"))
import replay_hf_individual_candidates as base
from run_true_daily_alpha import solve
from deep_strategies import concentrate_positive,liquid_mask
from suite_core import CAMPAIGNS,load_panels
base.OUT=CAMPAIGNS/"CAM-0617"/"artifacts"/"RUN-0027"; base.RUN=CAMPAIGNS/"CAM-0617"/"runs"/"RUN-0027.yaml"; base.SOURCE_RUN_BY_CAMPAIGN={"CAM-0617":"RUN-0026"}; base.EXTRA_QUOTE_DIRS=[CAMPAIGNS/"CAM-0600"/"artifacts"/"RUN-0031"]
def vs():
 p=load_panels()["qqq"]; w=concentrate_positive(solve(p,20,5),10,liquid_mask(p,.5))
 class V: pass
 v=V();v.variant_id="qqq__alpha_M20_E5__true_daily__top10__trend0";v.panel=p;v.weights=w;v.execution_lag=1;v.holding="open_to_next_open";return {"CAM-0617":v}
base.variants=vs
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("phase",choices=["ledgers","replay"]);a=p.parse_args();base.build_ledgers() if a.phase=="ledgers" else base.replay()
