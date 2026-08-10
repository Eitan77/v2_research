from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0600"/"artifacts"/"RUN-0024"
from suite_core import load_panels

def main():
 OUT.mkdir(parents=True,exist_ok=True); panels=load_panels(); rows=[]
 for name in ("qqq","sp500"):
  p=panels[name]
  for i,j in zip(*np.where(np.isfinite(p.split_grid)&(np.abs(p.split_grid-1)>1e-12))):
   if i==0: continue
   raw_prev_open,raw_event_open=p.raw_open[i-1,j],p.raw_open[i,j]; adj_prev_open,adj_event_open=p.adj_open[i-1,j],p.adj_open[i,j]; adj_prev_close=p.adj_close[i-1,j]
   rows.append({"panel":name,"symbol":str(p.symbols[j]),"event_date":str(p.dates[i].date()),"share_multiplier":float(p.split_grid[i,j]),"raw_event_open_over_previous_open":float(raw_event_open/raw_prev_open) if raw_prev_open>0 and np.isfinite(raw_event_open) else None,"adjusted_event_open_over_previous_open_return":float(adj_event_open/adj_prev_open-1) if adj_prev_open>0 and np.isfinite(adj_event_open) else None,"adjusted_event_open_over_previous_close_gap":float(adj_event_open/adj_prev_close-1) if adj_prev_close>0 and np.isfinite(adj_event_open) else None})
 frame=pd.DataFrame(rows); frame["hard_flag"]=(frame.adjusted_event_open_over_previous_close_gap.abs()>.50)|frame.adjusted_event_open_over_previous_close_gap.isna(); duplicates=frame.duplicated(["panel","symbol","event_date"],keep=False); report={"status":"completed_with_flags" if frame.hard_flag.any() or duplicates.any() else "completed_passed","run_id":"RUN-0024","event_rows":len(frame),"unique_panel_symbol_dates":int(frame[["panel","symbol","event_date"]].drop_duplicates().shape[0]),"hard_flags":int(frame.hard_flag.sum()),"duplicate_rows":int(duplicates.sum()),"maximum_absolute_adjusted_gap":float(frame.adjusted_event_open_over_previous_close_gap.abs().max()),"flagged_events":frame[frame.hard_flag|duplicates].to_dict("records"),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Corporate-action integrity audit; any hard flag blocks affected strategy interpretation pending diagnosis."}; frame.to_csv(OUT/"split_event_audit.csv",index=False); (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); path=CAM/"CAM-0600"/"runs"/"RUN-0024.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]=report["status"]; run["result"]=report; run["decision"]="Proceed only if no unresolved hard flags or duplicate multipliers remain."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0600"/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":"RUN-0024","event":report["status"],"hard_flags":report["hard_flags"],"duplicate_rows":report["duplicate_rows"],"holdout_rows_loaded":0})+"\n")
 print(json.dumps(report,indent=2))
if __name__=="__main__": main()
