from __future__ import annotations
import json
import numpy as np
import pandas as pd
from deep_strategies import build_deep_variants
from repair_strategies import build_repair_variants
from run_suite import _load_or_build_fundamentals,_preflight
from suite_core import CAMPAIGNS,load_panels,write_json

SH=CAMPAIGNS/"CAM-0600"/"artifacts"/"shared"; REPAIR={"CAM-0608","CAM-0609","CAM-0617","CAM-0621"}
def main():
 panels=load_panels(); pre=_preflight(panels); f,cov=_load_or_build_fundamentals(panels); deep=pd.read_csv(SH/"deep_diagnostic_summary.csv"); repair=pd.read_csv(SH/"repair_diagnostic_summary.csv"); sel={r.campaign_id:r.selected_variant for r in deep.itertuples() if isinstance(r.selected_variant,str) and r.selected_variant}; sel.update({r.campaign_id:r.selected_variant for r in repair.itertuples() if isinstance(r.selected_variant,str) and r.selected_variant})
 rows=[]
 for cid in [f"CAM-{n:04d}" for n in range(600,625)]:
  vid=sel.get(cid)
  if not vid: rows.append({"campaign_id":cid,"selected_variant":None,"status":"no_structured_survivor"}); continue
  vv=build_repair_variants(cid,panels,f) if cid in REPAIR else build_deep_variants(cid,panels,f); v=next(x for x in vv if x.variant_id==vid); p=v.panel; target=np.abs(v.weights)>1e-12; active=target.any(axis=1); eligible=p.member&np.isfinite(p.adj_open)&np.isfinite(p.adj_close); names=target.sum(axis=1)
  rows.append({"campaign_id":cid,"selected_variant":vid,"status":"selected_development_candidate","panel":p.name,"panel_dates":p.n_dates,"panel_symbols":p.n_symbols,"eligible_member_cells":int(eligible.sum()),"nonzero_target_cells":int(target.sum()),"target_cell_fraction_of_eligible":float(target.sum()/eligible.sum()) if eligible.sum() else None,"active_target_dates":int(active.sum()),"active_target_date_fraction":float(active.mean()),"average_names_per_active_date":float(names[active].mean()) if active.any() else 0,"maximum_names":int(names.max()),"generated_family_variant_count":len(vv),"maximum_loaded_date":str(p.dates.max().date()),"holdout_rows_loaded":0})
 frame=pd.DataFrame(rows); frame.to_csv(SH/"selected_candidate_attrition.csv",index=False); write_json(SH/"selected_candidate_attrition_report.json",{"status":"completed","campaigns":25,"selected":len(sel),"no_survivor":25-len(sel),"preflight":pre,"fundamental_coverage":cov,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}); print(frame.to_string(index=False))
if __name__=="__main__": main()
