from pathlib import Path
import json
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; SHARED=CAM/"CAM-0600"/"artifacts"/"shared"; csv=SHARED/"split_repaired_25_strategy_checkpoint.csv"; frame=pd.read_csv(csv)
frame["strategy_section"]=frame["strategy_section"].astype("object")
if "strategy_title" not in frame.columns: frame["strategy_title"]=None
frame["strategy_title"]=frame["strategy_title"].astype("object")
for i,row in frame.iterrows():
 plan=yaml.safe_load((CAM/row.campaign_id/"PLAN.yaml").read_text(encoding="utf-8")); frame.loc[i,"strategy_section"]=plan.get("paper_section"); frame.loc[i,"strategy_title"]=plan.get("title"); results_path=CAM/row.campaign_id/"RESULTS.yaml"; results=yaml.safe_load(results_path.read_text(encoding="utf-8")); results["split_repaired_checkpoint"]["strategy_section"]=plan.get("paper_section"); results["split_repaired_checkpoint"]["strategy_title"]=plan.get("title"); results_path.write_text(yaml.safe_dump(results,sort_keys=False),encoding="utf-8")
frame.to_csv(csv,index=False); (SHARED/"split_repaired_25_strategy_checkpoint.json").write_text(json.dumps(frame.where(frame.notna(),None).to_dict("records"),indent=2)+"\n",encoding="utf-8")
