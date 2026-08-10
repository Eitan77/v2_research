from __future__ import annotations
import hashlib,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
FILES=[
"campaigns/CAM-0600/COMPREHENSIVE_REPORT_SPLIT_REPAIRED.md",
"campaigns/CAM-0600/SPLIT_REPAIR_CONTRACT.yaml",
"campaigns/CAM-0600/SPLIT_REPAIRED_QUOTE_REPAIR_CONTRACT.yaml",
"campaigns/CAM-0600/artifacts/shared/split_repaired_25_strategy_checkpoint.csv",
"campaigns/CAM-0600/artifacts/shared/split_repaired_diagnostic_summary.csv",
"campaigns/CAM-0600/artifacts/shared/split_repaired_repair_diagnostic_summary.csv",
"campaigns/CAM-0600/artifacts/shared/split_repaired_quote_metrics_RUN-0023.csv",
"campaigns/CAM-0600/artifacts/RUN-0020/execution_report.json",
"campaigns/CAM-0600/artifacts/RUN-0024/execution_report.json",
"campaigns/CAM-0621/artifacts/RUN-0021/execution_report.json",
"campaigns/CAM-0625/INVALIDATION_SPLIT_ADJUSTMENT.md",
"campaigns/CAM-0625/RESULTS.yaml",
"campaigns/CAM-0625/REVIEW.md",
"campaigns/CAM-0625/artifacts/RUN-0017/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0018/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0019/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0020/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0021/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0022/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0023/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0024/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0025/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0026/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0027/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0028/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0029/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0030/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0030/variant_window_metrics.parquet",
"campaigns/CAM-0625/artifacts/RUN-0031/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0031/displayed_size_roles.parquet",
"campaigns/CAM-0625/artifacts/RUN-0032/execution_report.json",
"campaigns/CAM-0625/artifacts/RUN-0032/trade_episode_daily_detail.parquet",
"campaigns/CAM-0625/artifacts/checkpoint_split_repaired/equity_comparison.png",
"campaigns/CAM-0625/artifacts/checkpoint_split_repaired/quote_monthly.png",
]
rows=[]
for rel in FILES:
 p=ROOT/rel; rows.append({"path":rel,"bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()})
payload={"checkpoint":"2026-08-10_ssrn_split_repaired","files":rows,"tests":{"scoped_pytest":"17 passed","compileall":"passed","repository_wide_collection":"not rerun; prior checkpoint blocked by unrelated legacy/reference import errors"},"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"promotion_ready":False,"invalid_lineage":"CAM-0600 through CAM-0625 evidence before reciprocal split repair"}
out=ROOT/"campaigns"/"CAM-0625"/"CHECKPOINT_MANIFEST.json"; out.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8"); print(out)
