from __future__ import annotations
import hashlib,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
FILES=[
"campaigns/CAM-0600/COMPREHENSIVE_REPORT.md",
"campaigns/CAM-0600/SOURCE_CONTRACT.yaml",
"campaigns/CAM-0600/artifacts/shared/deep_candidate_audit.csv",
"campaigns/CAM-0600/artifacts/shared/control_increment_summary.csv",
"campaigns/CAM-0600/artifacts/shared/selected_candidate_attrition.csv",
"campaigns/CAM-0600/artifacts/shared/target_change_quote_metrics.csv",
"campaigns/CAM-0600/artifacts/shared/target_change_quote_path_audit.csv",
"campaigns/CAM-0625/PLAN.yaml",
"campaigns/CAM-0625/RESULTS.yaml",
"campaigns/CAM-0625/artifacts/RUN-0003/stress_metrics.csv",
"campaigns/CAM-0625/artifacts/RUN-0005/full_history_robustness.csv",
"campaigns/CAM-0625/artifacts/RUN-0007/delay_stress_metrics.csv",
"campaigns/CAM-0625/artifacts/RUN-0008/displayed_nbbo_capacity.csv",
"campaigns/CAM-0625/artifacts/RUN-0010/factor_exposure.csv",
"campaigns/CAM-0625/artifacts/RUN-0012/universe_substitution.csv",
"campaigns/CAM-0625/artifacts/RUN-0013/block_bootstrap.csv",
]
rows=[]
for rel in FILES:
 p=ROOT/rel; rows.append({"path":rel,"bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()})
payload={"checkpoint":"2026-08-10_ssrn_deep_development","files":rows,"tests":{"scoped_pytest":"7 passed","repository_wide_collection":"blocked by 27 unrelated legacy/reference import errors"},"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"promotion_ready":False}
out=ROOT/"campaigns"/"CAM-0625"/"CHECKPOINT_MANIFEST.json"; out.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8"); print(out)
