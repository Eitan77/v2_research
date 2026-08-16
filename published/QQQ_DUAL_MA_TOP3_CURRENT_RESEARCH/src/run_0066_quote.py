from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import run_0034_quote as quote_engine
from run_0033_exit_overlays import base_context

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0066"
IDS = ("control", "entry_veto_10pct", "entry_veto_20pct", "entry_veto_30pct", "entry_veto_40pct", "entry_veto_50pct")


def weights():
    p = base_context()[0]
    return p, {name: np.load(OUT / f"weights_{name}.npy") for name in IDS}


quote_engine.OUT = OUT
quote_engine.IDS = IDS
quote_engine.weights = weights
_raw_cache = quote_engine.cache


def valid_cache(label):
    frame = _raw_cache(label)
    valid = (frame.quote_ts.notna() & frame.bid_price.notna() & frame.ask_price.notna()
             & (frame.bid_price > 0) & (frame.ask_price >= frame.bid_price))
    return frame.loc[valid].copy()


quote_engine.cache = valid_cache


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("ledgers", "missing", "replay"))
    args = parser.parse_args()
    getattr(quote_engine, args.phase)()
