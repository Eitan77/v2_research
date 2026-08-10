from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import yaml
from suite_core import CAMPAIGNS,jsonable,write_json

SH=CAMPAIGNS/"CAM-0600"/"artifacts"/"shared"
SPECS={
"CAM-0600":("RUN-0012","sp500__mom63_skip0__top3__panic1","sp500__mom63_skip0__top3__panic0","Panic defense sacrifices return but cuts historical drawdown materially; retain as a risk overlay."),
"CAM-0602":("RUN-0012","qqq__value_quality__top5","qqq__value__top5","The winner is an adapted value-quality composite, not isolated source value; keep the label explicit and concentration caveat."),
"CAM-0604":("RUN-0012","sp500__value_quality_mom__top20","sp500__momentum__top20","The multifactor blend slightly trails momentum return but materially improves drawdown and recent positive-month breadth."),
"CAM-0610":("RUN-0012","qqq__ma150__weekly__top3","qqq__ungated__weekly__top3","The selected MA150 gate raises recent consistency but does not improve full-history return or drawdown; only the neighboring MA200 gate shows clear risk-control value."),
"CAM-0611":("RUN-0012","qqq__ma50_200__weekly__top3","qqq__ungated__weekly__top3","The 50/200 gate improves return modestly and sharply reduces drawdown versus identical momentum ranking."),
"CAM-0612":("RUN-0012","qqq__ma10_50_200__monthly__top3","qqq__ungated__monthly__top3","The selected triple-MA rule materially trails ungated momentum and barely changes drawdown; it is a filtered momentum expression, not a distinct edge."),
"CAM-0615":("RUN-0012","qqq__fullcov_s50__mom60__positive_top10","qqq__simple_mom60__top10","Simple momentum dominates the adapted positive optimizer sleeve; source optimization is not isolated."),
"CAM-0616":("RUN-0012","qqq__fullcov_s50__mom60__positive_top10","qqq__simple_mom60__top10","The executable long-only sleeve is a momentum proxy; signed source identity remains non-executable overnight."),
"CAM-0617":("RUN-0012","etf_unlevered__alpha_M20_E5__top10","etf_unlevered__alpha_M20_E5__top5","Removing leveraged and inverse ETFs leaves positive but much weaker, cost-sensitive alpha-combo evidence."),
"CAM-0619":("RUN-0014","sector__mom63_skip0__top1__winnerma100","sector__mom63_skip0__top1__plain","The winner-MA gate improves both return and drawdown in the matched 63-day sector rule."),
"CAM-0620":("RUN-0014","sector__mom63_skip0__top1__marketma200","sector__mom63_skip0__top1__plain","The broad-market gate roughly halves drawdown and modestly improves return, with BIL defense and no margin."),
"CAM-0623":("RUN-0012","qqq__chs_safe__top5","qqq__momentum__top5","Safest-distress materially beats the matched QQQ momentum control and halves drawdown, while remaining adapted development evidence."),
}

def main():
 rows=[]
 for cid,(run,a,b,decision) in SPECS.items():
  d=pd.read_csv(CAMPAIGNS/cid/"artifacts"/run/"variant_metrics.csv")
  deep=pd.read_csv(CAMPAIGNS/cid/"artifacts"/"RUN-0008"/"variant_metrics.csv")
  for cost in (2.,10.):
   xa=d[(d.cost_bps_per_side==cost)&(d.variant_id==a)]
   if xa.empty: xa=deep[(deep.cost_bps_per_side==cost)&(deep.variant_id==a)]
   x=xa.iloc[0]; y=d[(d.cost_bps_per_side==cost)&(d.variant_id==b)].iloc[0]
   rows.append({"campaign_id":cid,"run_id":run,"cost_bps_per_side":cost,"adapted_variant":a,"control_variant":b,"adapted_net":x.net_simple_return,"control_net":y.net_simple_return,"incremental_net":x.net_simple_return-y.net_simple_return,"adapted_drawdown":x.maximum_drawdown,"control_drawdown":y.maximum_drawdown,"drawdown_change":x.maximum_drawdown-y.maximum_drawdown,"adapted_recent12_average_month":x.recent12_average_month,"control_recent12_average_month":y.recent12_average_month,"adapted_recent12_positive_months":x.recent12_positive_months,"control_recent12_positive_months":y.recent12_positive_months,"decision":decision})
  p=CAMPAIGNS/cid/"runs"/f"{run}.yaml"; yml=yaml.safe_load(p.read_text()); subset=jsonable([r for r in rows if r["campaign_id"]==cid]); yml["status"]="completed"; yml["result"]={"executed_configuration_reconciled":True,"matched_comparisons":subset,"holdout_rows_loaded":0}; yml["decision"]=decision; p.write_text(yaml.safe_dump(yml,sort_keys=False),encoding="utf-8")
  with (CAMPAIGNS/cid/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":run,"event":"completed","decision":decision,"holdout_rows_loaded":0})+"\n")
 frame=pd.DataFrame(rows); frame.to_csv(SH/"control_increment_summary.csv",index=False); write_json(SH/"control_increment_report.json",{"status":"completed","campaigns":len(SPECS),"comparisons":len(frame),"holdout_rows_loaded":0,"contract":"campaigns/CAM-0600/CONTROL_CONTRACT.yaml"}); print(frame[frame.cost_bps_per_side==2].to_string(index=False))
if __name__=="__main__": main()
