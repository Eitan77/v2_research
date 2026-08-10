from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

from analyze_deep_development import block_metrics
from suite_core import CAMPAIGNS


CAMPAIGN_IDS=("CAM-0606","CAM-0608","CAM-0609","CAM-0613","CAM-0617","CAM-0621")
SHARED=CAMPAIGNS/"CAM-0600"/"artifacts"/"shared"


def daily_path(campaign_id,variant_id):
    safe=f"{variant_id}__cost_2bps".replace("/","_").replace(":","_")
    return CAMPAIGNS/campaign_id/"artifacts"/"RUN-0010"/"variants"/safe/"daily.parquet"


def main():
    rows=[]
    for campaign_id in CAMPAIGN_IDS:
        d=pd.read_csv(CAMPAIGNS/campaign_id/"artifacts"/"RUN-0010"/"variant_metrics.csv")
        x=d[d.cost_bps_per_side==2].copy().merge(d[d.cost_bps_per_side==10][["variant_id","net_simple_return"]].rename(columns={"net_simple_return":"net10"}),on="variant_id",validate="one_to_one")
        b=[block_metrics(daily_path(campaign_id,v)) for v in x.variant_id]
        x["positive_blocks"]=[z["positive_blocks"] for z in b]; x["worst_block_average_month"]=[z["worst_block_average_month"] for z in b]
        x["structured_screen"]=(x.net_simple_return.gt(0)&x.net10.gt(0)&x.recent12_average_month.gt(0)&x.recent12_positive_months.ge(8)&x.recent18_positive_months.ge(11)&x.maximum_drawdown.le(.40)&x.top5_day_positive_share.fillna(1).le(.15)&x.entries.ge(10)&x.positive_blocks.ge(2))
        good=x[x.structured_screen].sort_values(["positive_blocks","worst_block_average_month","recent18_average_month","net_simple_return"],ascending=False)
        if len(good):
            chosen=good.iloc[0]; selected=str(chosen.variant_id); decision="provisional_target_change_quote_gate"
        else:
            chosen=x.sort_values("net_simple_return",ascending=False).iloc[0]; selected=None; decision="repair_failed_no_structured_survivor"
        row={"campaign_id":campaign_id,"executed_variants":len(x),"structured_survivors":len(good),"selected_variant":selected,"decision":decision,"selected_2bps":float(chosen.net_simple_return) if selected else None,"selected_10bps":float(chosen.net10) if selected else None,"selected_recent12_average":float(chosen.recent12_average_month) if selected else None,"selected_recent12_positive":int(chosen.recent12_positive_months) if selected else None,"selected_recent18_average":float(chosen.recent18_average_month) if selected else None,"selected_recent18_positive":int(chosen.recent18_positive_months) if selected else None,"selected_maximum_drawdown":float(chosen.maximum_drawdown) if selected else None,"selected_positive_blocks":int(chosen.positive_blocks) if selected else None,"selected_worst_block_average_month":float(chosen.worst_block_average_month) if selected else None,"holdout_rows_loaded":0}; rows.append(row)
        path=CAMPAIGNS/campaign_id/"runs"/"RUN-0010.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="completed"; run["result"]=row; run["decision"]=decision; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
        with (CAMPAIGNS/campaign_id/"WORKLOG.jsonl").open("a",encoding="utf-8") as fh: fh.write(json.dumps({"run_id":"RUN-0010","status":"completed","decision":decision,"selected_variant":selected,"holdout_rows_loaded":0},sort_keys=True)+"\n")
    out=pd.DataFrame(rows); out.to_csv(SHARED/"repair_diagnostic_summary.csv",index=False); print(out.to_string(index=False))


if __name__=="__main__": main()
