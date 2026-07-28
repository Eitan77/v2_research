from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd

from .config import ScanConfig
from .registry import FeatureSpec
from .scanner import scan


def exact_pair(
    feature_path: Path,
    target_path: Path,
    spec: FeatureSpec,
    target: str,
    config: ScanConfig,
    direction_hint: float | None = None,
) -> tuple[dict | None, list[dict]]:
    identifiers=["symbol","session_date","decision_ts"]
    feature_frame=pd.read_parquet(feature_path,columns=[*identifiers,spec.name])
    target_frame=pd.read_parquet(target_path,columns=[target])
    frame=pd.concat([feature_frame.reset_index(drop=True),target_frame.reset_index(drop=True)],axis=1)
    exact,tables=scan(frame,[spec],[target],replace(config,use_cuda=False),None,skip_dense=True,direction_hint=direction_hint)
    row=exact.iloc[0].to_dict() if not exact.empty else None
    table=tables.get((spec.name,target),pd.DataFrame()).to_dict("records")
    return row,table
