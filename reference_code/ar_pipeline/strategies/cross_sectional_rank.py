from __future__ import annotations

from pathlib import Path
from typing import Any

from ar_pipeline.data import connect_catalog
from ar_pipeline.data import _rth_sql_predicates
from ar_pipeline.data import _session_name
from ar_pipeline.execution import WorkloadInfo
from ar_pipeline.engines.cuda_discovery import run_discovery as run_rank_discovery


def estimate_workload(config: dict[str, Any]) -> WorkloadInfo:
    scan = config.get("scan", {})
    formulas = int(scan.get("formulas", 512))
    top_ns = scan.get("top_ns", [1, 2, 3])
    costs = scan.get("cost_bps_per_side_grid", [scan.get("cost_bps_per_side", 5.0)])
    estimated_rows = _estimate_rows(config)
    return WorkloadInfo(
        pattern="dense_tensor_cross_sectional_rank",
        preferred_device="cuda",
        supports_cuda=True,
        supports_cpu=True,
        supports_batch_autotune=True,
        estimated_rows=estimated_rows,
        estimated_candidates=formulas * len(top_ns) * len(costs),
    )


def run(config: dict[str, Any], output_dir: Path) -> dict[str, str]:
    result = run_rank_discovery(config, output_dir)
    return {
        "leaderboard": str(output_dir / "leaderboard.csv"),
        "leaderboard_parquet": str(output_dir / "leaderboard.parquet"),
        "cost_sensitivity": str(output_dir / "cost_sensitivity.csv"),
        "trades": str(output_dir / "discovery_trades.parquet"),
        "report": str(output_dir / "discovery_report.md"),
    }


def _estimate_rows(config: dict[str, Any]) -> int | None:
    data_cfg = config.get("data", {})
    scan = config.get("scan", {})
    table = data_cfg.get("table", "research_matrix")
    horizon = int(scan.get("horizon", 1))
    label_col = f"fwd_return_{horizon}"
    where = ["timeframe = ?"]
    params: list[Any] = [scan.get("timeframe", "15m")]
    if scan.get("train_start"):
        where.append("cast(timestamp as timestamp) >= ?")
        params.append(scan["train_start"])
    if scan.get("train_end"):
        where.append("cast(timestamp as timestamp) <= ?")
        params.append(scan["train_end"])
    if scan.get("universe") == "qqq_pit":
        where.append("coalesce(is_qqq_member, false)")
    if _session_name(scan) == "rth":
        where.extend(_rth_sql_predicates(horizon, str(scan.get("timeframe", "15m"))))
    where.append(f"{label_col} is not null")
    con = connect_catalog(data_cfg.get("catalog_path"))
    try:
        return int(con.execute(f"select count(*) from {table} where {' and '.join(where)}", params).fetchone()[0])
    finally:
        con.close()
