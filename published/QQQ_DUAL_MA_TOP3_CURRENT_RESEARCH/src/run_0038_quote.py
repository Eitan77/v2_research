from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import run_0034_quote as q
from run_0033_exit_overlays import base_context
ROOT=Path(__file__).resolve().parents[3];q.OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0038";q.IDS=("monday","thursday","friday")
def weights():
 p=base_context()[0];return p,{n:np.load(q.OUT/f"weights_{n}.npy") for n in q.IDS}
q.weights=weights
if __name__=="__main__":
 ap=argparse.ArgumentParser();ap.add_argument("phase",choices=("ledgers","missing","replay"));a=ap.parse_args();getattr(q,a.phase)()
