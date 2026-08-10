from __future__ import annotations
from pathlib import Path
import json
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; SHARED=CAM/"CAM-0600"/"artifacts"/"shared"
base=pd.read_csv(SHARED/"split_repaired_diagnostic_summary.csv").set_index("campaign_id"); repair=pd.read_csv(SHARED/"split_repaired_repair_diagnostic_summary.csv").set_index("campaign_id"); q=pd.read_csv(SHARED/"split_repaired_quote_metrics_RUN-0023.csv"); rows=[]
for number in range(600,625):
 cid=f"CAM-{number:04d}"; source_run="RUN-0021" if cid in repair.index and pd.notna(repair.loc[cid,"selected_variant"]) else "RUN-0020"; source=(repair if source_run=="RUN-0021" else base).loc[cid]; variant=str(source.selected_variant) if pd.notna(source.selected_variant) else None; plan=yaml.safe_load((CAM/cid/"PLAN.yaml").read_text(encoding="utf-8")); row={"campaign_id":cid,"strategy_section":plan.get("paper_section"),"strategy_title":plan.get("title"),"source_run":source_run,"structured_survivors":int(source.structured_survivors),"selected_variant":variant,"bar_net_2bps":float(source.selected_2bps) if pd.notna(source.selected_2bps) else None,"bar_maximum_drawdown":float(source.selected_maximum_drawdown) if pd.notna(source.selected_maximum_drawdown) else None,"decision":"provisional_execution_survivor" if variant else "no_structured_survivor"}
 for extra,label in ((2,"quote_2bps"),(10,"quote_10bps")):
  x=q[(q.campaign_id==cid)&(q.clock.astype(str).str.zfill(4)=="0940")&(q.extra_slippage_bps_per_side==extra)]
  row[f"{label}_net"]=float(x.net_simple_return.iloc[0]) if len(x) else None; row[f"{label}_drawdown"]=float(x.maximum_drawdown.iloc[0]) if len(x) else None; row[f"{label}_positive_months"]=int(x.positive_months.iloc[0]) if len(x) else None; row[f"{label}_negative_months"]=int(x.negative_months.iloc[0]) if len(x) else None; row[f"{label}_coverage"]=float(x.role_coverage.iloc[0]) if len(x) else None
 rows.append(row)
 results_path=CAM/cid/"RESULTS.yaml"; results=yaml.safe_load(results_path.read_text(encoding="utf-8")) or {}; results["prior_checkpoint_status"]="invalid_split_adjustment"; results["split_repaired_checkpoint"]={k:v for k,v in row.items() if k!="campaign_id"}; results["split_repaired_checkpoint"]["maximum_loaded_date"]="2026-04-30"; results["split_repaired_checkpoint"]["holdout_rows_loaded"]=0; results["split_repaired_checkpoint"]["promotion_ready"]=False; results_path.write_text(yaml.safe_dump(results,sort_keys=False),encoding="utf-8")
 review_path=CAM/cid/"REVIEW.md"; note=f"\n\n## Split-repaired checkpoint (RUN-0020/RUN-0021/RUN-0023)\n\nPrior strategy evidence is invalid because the inherited stock panel adjusted forward splits in the wrong direction. The repaired structured result is **{row['decision']}**"+(f" using `{variant}`. Its full repaired 2 bp additive return is {row['bar_net_2bps']:.1%} with {row['bar_maximum_drawdown']:.1%} maximum drawdown; 09:40 SIP replay at +2 bp is {row['quote_2bps_net']:.1%} with {row['quote_2bps_drawdown']:.1%} drawdown and {row['quote_2bps_positive_months']}/{row['quote_2bps_negative_months']} positive/negative months." if variant else ". No mechanism-consistent repair cleared the structured screen.")+" This remains adapted development evidence; the May 2026 holdout was not accessed and promotion is blocked.\n"
 with review_path.open("a",encoding="utf-8") as f: f.write(note)
pd.DataFrame(rows).to_csv(SHARED/"split_repaired_25_strategy_checkpoint.csv",index=False)
(SHARED/"split_repaired_25_strategy_checkpoint.json").write_text(json.dumps(rows,indent=2)+"\n",encoding="utf-8")
print(pd.DataFrame(rows)[["campaign_id","selected_variant","bar_net_2bps","bar_maximum_drawdown","quote_2bps_net","quote_2bps_drawdown","quote_2bps_positive_months","quote_2bps_negative_months","decision"]].to_string(index=False))
