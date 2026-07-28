from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ScanConfig:
    catalog_path: str = "D:/AlgoResearch/data/catalog.duckdb"
    source_table: str = "derived_bars_5m"
    feed: str = "sip"
    adjustment: str = "raw"
    start: str = "2019-01-01"
    discovery_end: str = "2026-04-30"  # sealed holdout begins 2026-05-01
    sealed_holdout_start: str = "2026-05-01"
    allow_holdout_access: bool = False
    universe: list[str] = field(default_factory=list)
    decision_times_et: list[str] = field(default_factory=list)
    lookbacks: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 24, 30, 36, 48, 60, 78])
    target_horizons_minutes: list[int] = field(default_factory=lambda: list(range(5, 390, 5)))
    quantiles: int = 10
    min_observations: int = 500
    min_sessions: int = 40
    min_symbols: int = 5
    min_bin_observations: int = 30
    outlier_policy: str = "none"  # none|winsorize_1pct
    output_root: str = "D:/AlgoResearch/Quant Pipeline/runs"
    experiment_id: str = "phase1_5m_discovery_through_20260430"
    benchmark_symbol: str = "QQQ"
    sector_map_path: str | None = None
    use_cuda: bool = True
    cuda_device: str = "cuda:0"
    scan_batch_rows: int = 250_000
    feature_chunk_size: int = 24
    feature_workers: int = 16
    exact_workers: int = 6
    target_chunk_size: int = 4
    resume: bool = True
    checkpoint_every_pairs: int = 25
    normalization_windows_sessions: list[int] = field(default_factory=lambda: [20, 60])
    opening_windows_minutes: list[int] = field(default_factory=lambda: [1, 3, 5, 10, 15, 20, 30, 45, 60, 90])
    cross_sectional_min_symbols: int = 5
    multiple_testing_grouping: str = "feature_family_target_family"
    sensitivity_outlier_policy: str = "winsorize_1pct"

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ScanConfig":
        values = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        allowed = set(cls.__dataclass_fields__)
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown config keys: {sorted(unknown)}")
        return cls(**values)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
