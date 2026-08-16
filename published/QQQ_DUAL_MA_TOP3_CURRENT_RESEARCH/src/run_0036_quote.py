from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import run_0034_quote as q
from run_0033_exit_overlays import base_context
ROOT=Path(__file__).resolve().parents[3];q.OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0036";q.IDS=("control_normal","control_ratchet","mom25_normal","mom25_ratchet")
def weights():
 p=base_context()[0]
 return p,{"control_normal":np.load(q.OUT/"weights_control_normal.npy"),"control_ratchet":np.load(q.OUT/"weights_control_stair15_5_2close.npy"),"mom25_normal":np.load(q.OUT/"weights_mom_floor25_normal.npy"),"mom25_ratchet":np.load(q.OUT/"weights_mom_floor25_stair15_5_2close.npy")}
q.weights=weights
if __name__=="__main__":
 ap=argparse.ArgumentParser();ap.add_argument("phase",choices=("ledgers","missing","replay"));a=ap.parse_args();getattr(q,a.phase)()
