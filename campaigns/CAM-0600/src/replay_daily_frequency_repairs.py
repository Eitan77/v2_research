import argparse
import replay_hf_individual_candidates as base
import run_daily_frequency_repairs as daily
from run_suite import _load_or_build_fundamentals
from suite_core import CAMPAIGNS, load_panels

base.OUT=CAMPAIGNS/"CAM-0600"/"artifacts"/"RUN-0031"
base.RUN=CAMPAIGNS/"CAM-0600"/"runs"/"RUN-0031.yaml"
base.SOURCE_RUN_BY_CAMPAIGN={x:"RUN-0024" for x in ("CAM-0608","CAM-0609","CAM-0611","CAM-0612","CAM-0617")}

def selected_variants():
 _,wanted=base.specs(); panels=load_panels(); f,_=_load_or_build_fundamentals(panels); out={}
 for cid,vid in wanted.items():
  matches=[x for x in daily.variants(cid,panels,f) if x[0]==vid]
  if len(matches)!=1: raise RuntimeError((cid,vid,len(matches)))
  name,p,w=matches[0]
  class V: pass
  v=V(); v.variant_id=name; v.panel=p; v.weights=w; v.execution_lag=1; v.holding="open_to_next_open"; out[cid]=v
 return out

base.variants=selected_variants
if __name__=="__main__":
 p=argparse.ArgumentParser(); p.add_argument("phase",choices=["ledgers","replay"]); a=p.parse_args(); base.build_ledgers() if a.phase=="ledgers" else base.replay()
