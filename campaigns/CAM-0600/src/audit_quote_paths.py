from __future__ import annotations
import json
import pandas as pd
from suite_core import CAMPAIGNS,write_json

SHARED=CAMPAIGNS/"CAM-0600"/"artifacts"/"shared"
REPAIR={"CAM-0608","CAM-0609","CAM-0617","CAM-0621"}

def main():
    metrics=pd.read_csv(SHARED/"target_change_quote_metrics.csv")
    metrics=metrics[(metrics.extra_slippage_bps_per_side==2)&(metrics.clock.astype(str).str.zfill(4)=="0940")]
    rows=[]; months=[]
    for r in metrics.itertuples(index=False):
        run="RUN-0011" if r.campaign_id in REPAIR else "RUN-0009"
        path=CAMPAIGNS/r.campaign_id/"artifacts"/run/"daily_0940_2bps_extra.parquet"
        d=pd.read_parquet(path); d["date"]=pd.to_datetime(d.date); s=d.set_index("date").net_pnl.sort_index()
        pos=s.clip(lower=0).sort_values(ascending=False); positive=float(pos.sum())
        m=s.groupby(s.index.to_period("M")).sum()
        rows.append({"campaign_id":r.campaign_id,"run_id":run,"net_simple_return":float(s.sum()),"maximum_drawdown":r.maximum_drawdown,"active_days":int((s.abs()>1e-12).sum()),"green_days":int((s>1e-12).sum()),"red_days":int((s<-1e-12).sum()),"top5_day_positive_share":float(pos.head(5).sum()/positive) if positive>0 else None,"best_day":float(s.max()),"worst_day":float(s.min()),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"monthly_std":float(m.std(ddof=1)),"worst_month":float(m.min()),"best_month":float(m.max()),"role_coverage":r.role_coverage})
        for period,value in m.items(): months.append({"campaign_id":r.campaign_id,"month":str(period),"net_pnl":float(value)})
    frame=pd.DataFrame(rows).sort_values("net_simple_return",ascending=False); frame.to_csv(SHARED/"target_change_quote_path_audit.csv",index=False); pd.DataFrame(months).to_csv(SHARED/"target_change_quote_monthly_0940_2bps.csv",index=False)
    write_json(SHARED/"target_change_quote_path_audit_report.json",{"status":"completed","clock":"09:40","additional_slippage_bps_per_side":2,"campaigns":len(frame),"holdout_rows_loaded":0,"best_return":frame.iloc[0].to_dict(),"lowest_drawdown_positive":frame[frame.net_simple_return>0].sort_values("maximum_drawdown").iloc[0].to_dict()})
    print(frame.to_string(index=False))
if __name__=="__main__": main()
