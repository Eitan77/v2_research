from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from suite_core import CAMPAIGNS, write_json


SHARED=CAMPAIGNS/"CAM-0600"/"artifacts"/"shared"
REPAIR_IDS={"CAM-0608","CAM-0609","CAM-0617","CAM-0621"}


def variant_daily(campaign_id,variant_id):
    run="RUN-0010" if campaign_id in REPAIR_IDS else "RUN-0008"
    safe=(variant_id+"__cost_2bps").replace("/","_").replace(":","_")
    d=pd.read_parquet(CAMPAIGNS/campaign_id/"artifacts"/run/"variants"/safe/"daily.parquet")
    d["date"]=pd.to_datetime(d.date); return d.set_index("date").sort_index()


def full_path_audit(daily):
    m=daily.net_pnl.groupby(daily.index.to_period("M")).sum()
    rolling=m.rolling(12,min_periods=12).sum().dropna()
    recent=float(m.iloc[-12:].sum())
    return {"full_net":float(daily.net_pnl.sum()),"maximum_drawdown":float(((1+daily.net_pnl.cumsum()).cummax()-(1+daily.net_pnl.cumsum())).div((1+daily.net_pnl.cumsum()).cummax()).max()),"months":len(m),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"recent12":recent,"recent12_percentile":float((rolling<=recent).mean()) if len(rolling) else None,"worst_rolling12":float(rolling.min()) if len(rolling) else None,"median_rolling12":float(rolling.median()) if len(rolling) else None,"positive_rolling12_fraction":float((rolling>0).mean()) if len(rolling) else None}


def main():
    deep=pd.read_csv(SHARED/"deep_diagnostic_summary.csv")
    repair=pd.read_csv(SHARED/"repair_diagnostic_summary.csv")
    selected=pd.concat([deep[deep.selected_variant.notna()][["campaign_id","selected_variant"]],repair[repair.selected_variant.notna()][["campaign_id","selected_variant"]]],ignore_index=True)
    quote=pd.read_csv(SHARED/"target_change_quote_metrics.csv")
    quote=quote[(quote.extra_slippage_bps_per_side==2)&(quote.clock.astype(str).str.zfill(4)=="0940")]
    rows=[]; paths={}; monthly={}
    for r in selected.itertuples(index=False):
        d=variant_daily(str(r.campaign_id),str(r.selected_variant)); audit=full_path_audit(d); paths[str(r.campaign_id)]=d.net_pnl; monthly[str(r.campaign_id)]=d.net_pnl.groupby(d.index.to_period("M")).sum()
        q=quote[quote.campaign_id==r.campaign_id].iloc[0]
        rows.append({"campaign_id":r.campaign_id,"variant_id":r.selected_variant,**audit,"quote0940_net_2bps_extra":q.net_simple_return,"quote0940_drawdown":q.maximum_drawdown,"quote0940_positive_months":q.positive_months,"quote0940_negative_months":q.negative_months,"quote_role_coverage":q.role_coverage})
    frame=pd.DataFrame(rows).sort_values("quote0940_net_2bps_extra",ascending=False); frame.to_csv(SHARED/"deep_candidate_audit.csv",index=False)
    common=pd.concat(paths,axis=1).fillna(0.0); corr=common.corr(); corr.to_csv(SHARED/"deep_candidate_daily_correlation.csv")
    pairs=[]
    for i,a in enumerate(corr.columns):
        for b in corr.columns[i+1:]: pairs.append({"campaign_a":a,"campaign_b":b,"daily_correlation":corr.loc[a,b]})
    pair_frame=pd.DataFrame(pairs).sort_values("daily_correlation",ascending=False); pair_frame.to_csv(SHARED/"deep_candidate_correlation_pairs.csv",index=False)
    tests=0
    for n in range(600,625): tests += len(pd.read_csv(CAMPAIGNS/f"CAM-{n:04d}"/"artifacts"/"RUN-0008"/"variant_metrics.csv").query("cost_bps_per_side==2"))
    for c in REPAIR_IDS|{"CAM-0606","CAM-0613"}: tests += len(pd.read_csv(CAMPAIGNS/c/"artifacts"/"RUN-0010"/"variant_metrics.csv").query("cost_bps_per_side==2"))
    write_json(SHARED/"deep_candidate_audit_report.json",{"status":"completed","selected_candidates":len(frame),"new_variant_definitions_tested":tests,"multiple_testing_warning":"All candidates are adapted development evidence; maxima are not p-values and do not establish independent discoveries.","correlation_pairs_above_0_70":int((pair_frame.daily_correlation>.70).sum()),"highest_correlations":pair_frame.head(20).to_dict("records"),"holdout_rows_loaded":0})
    print(frame.to_string(index=False)); print("\nHIGHEST CORRELATIONS\n",pair_frame.head(30).to_string(index=False))


if __name__=="__main__": main()
