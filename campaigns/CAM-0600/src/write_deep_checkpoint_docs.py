from __future__ import annotations
from pathlib import Path
import pandas as pd
import yaml
from suite_core import CAMPAIGNS

SH=CAMPAIGNS/"CAM-0600"/"artifacts"/"shared"
OUTCOME={
"CAM-0600":"recent_momentum_component_promising_unpromoted","CAM-0601":"positive_but_inconsistent_unpromoted","CAM-0602":"strong_adapted_factor_concentrated_unpromoted","CAM-0603":"low_risk_diversifier_unpromoted","CAM-0604":"smooth_multifactor_component_unpromoted","CAM-0605":"fragile_high_drawdown_unpromoted","CAM-0606":"retired_mechanism_exhausted","CAM-0607":"execution_sensitive_high_drawdown_unpromoted","CAM-0608":"fragile_residual_duplicate_unpromoted","CAM-0609":"residual_duplicate_unpromoted","CAM-0610":"filtered_momentum_duplicate_unpromoted","CAM-0611":"two_ma_risk_filter_supported_unpromoted","CAM-0612":"filtered_momentum_duplicate_unpromoted","CAM-0613":"retired_mechanism_exhausted","CAM-0614":"small_delay_sensitive_edge_unpromoted","CAM-0615":"optimizer_not_isolated_unpromoted","CAM-0616":"source_signed_nonexecutible_adaptation_only","CAM-0617":"leveraged_concentrated_tactical_unpromoted","CAM-0618":"smooth_sector_diversifier_unpromoted","CAM-0619":"winner_ma_gate_supported_unpromoted","CAM-0620":"dual_market_gate_supported_unpromoted","CAM-0621":"low_drawdown_execution_sensitive_diversifier","CAM-0622":"modest_vol_target_unpromoted","CAM-0623":"strong_distress_component_unpromoted","CAM-0624":"low_drawdown_distress_component_unpromoted"}

def main():
 src=yaml.safe_load((CAMPAIGNS/"CAM-0600"/"SOURCE_CONTRACT.yaml").read_text()); deep=pd.read_csv(SH/"deep_candidate_audit.csv").set_index("campaign_id"); quote=pd.read_csv(SH/"target_change_quote_path_audit.csv").set_index("campaign_id"); attr=pd.read_csv(SH/"selected_candidate_attrition.csv").set_index("campaign_id"); controls=pd.read_csv(SH/"control_increment_summary.csv"); controls=controls[controls.cost_bps_per_side==2].set_index("campaign_id")
 for i,(section,spec) in enumerate(src["sections"].items()):
  cid=f"CAM-{600+i:04d}"; result_path=CAMPAIGNS/cid/"RESULTS.yaml"; old=yaml.safe_load(result_path.read_text()) if result_path.exists() else {"campaign_id":cid}
  checkpoint={"checkpoint":"2026-08-10_deep_development","paper_section":section,"strategy":spec["name"],"status":OUTCOME[cid],"promotion_ready":False,"development_only":True,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"broker_margin_used":False}
  if cid in deep.index:
   a=deep.loc[cid]; q=quote.loc[cid]; t=attr.loc[cid]
   checkpoint["selected"]={"variant":str(a.variant_id),"full_history_net_2bps":float(a.full_net),"full_history_maximum_drawdown":float(a.maximum_drawdown),"recent12_net_2bps":float(a.recent12),"recent12_historical_percentile":float(a.recent12_percentile),"positive_rolling12_fraction":float(a.positive_rolling12_fraction),"target_active_date_fraction":float(t.active_target_date_fraction),"average_names_per_active_date":float(t.average_names_per_active_date)}
   checkpoint["quote_0940_target_change"]={"additional_slippage_bps_per_side":2,"net_simple_return":float(q.net_simple_return),"maximum_drawdown":float(q.maximum_drawdown),"positive_months":int(q.positive_months),"negative_months":int(q.negative_months),"active_days":int(q.active_days),"top5_day_positive_share":float(q.top5_day_positive_share),"role_coverage":float(q.role_coverage)}
  else: checkpoint["selected"]=None; checkpoint["quote_0940_target_change"]=None
  checkpoint["matched_control"]=None if cid not in controls.index else {k:(float(v) if isinstance(v,(int,float)) and pd.notna(v) else str(v)) for k,v in controls.loc[cid].to_dict().items()}
  checkpoint["conclusion"]="No strategy is promoted; evidence is adapted development data and the sealed holdout remains untouched."
  old["deep_development_checkpoint"]=checkpoint; result_path.write_text(yaml.safe_dump(old,sort_keys=False,allow_unicode=True),encoding="utf-8")
  review=CAMPAIGNS/cid/"REVIEW.md"; header="## 2026-08-10 deep-development checkpoint"; prior=review.read_text(encoding="utf-8") if review.exists() else f"# {cid} review\n"
  if header in prior: continue
  lines=["",header,"",f"Paper section {section}, **{spec['name']}**. Source contract: {spec['exact']}",""]
  if cid in deep.index:
   a=deep.loc[cid]; q=quote.loc[cid]; t=attr.loc[cid]; lines += [f"The structured survivor `{a.variant_id}` earned {a.full_net:+.1%} net at 2 bps over its available development history and {a.recent12:+.1%} in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned {q.net_simple_return:+.1%} with {q.maximum_drawdown:.1%} drawdown, {int(q.positive_months)}/{int(q.negative_months)} positive/negative months, and {q.top5_day_positive_share:.1%} of positive P&L from the best five days.","",f"Selection activity covered {t.active_target_date_fraction:.1%} of dates and averaged {t.average_names_per_active_date:.2f} names when active. Status: `{OUTCOME[cid]}`."]
  else: lines += ["No structured survivor cleared the mechanism, cost, and recent-consistency screen after the repair loop.","",f"Status: `{OUTCOME[cid]}`."]
  if cid in controls.index: lines += ["",f"Matched-control conclusion: {controls.loc[cid].decision}"]
  lines += ["","This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.",""]
  review.write_text(prior.rstrip()+"\n"+"\n".join(lines),encoding="utf-8")
 print("updated 25 RESULTS.yaml and REVIEW.md checkpoint sections")
if __name__=="__main__": main()
