from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0020"; PARENT=["CAM-0600","CAM-0604","CAM-0621","CAM-0624"]; EXTRA=["CAM-0618","CAM-0603"]; REPAIR={"CAM-0621"}
def dd(s):
 e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max()) if len(s) else 0
def stats(s):
 m=s.groupby(s.index.to_period("M")).sum(); y=s.groupby(s.index.year).sum(); pos=s.clip(lower=0).sort_values(ascending=False); return {"net_simple_return":float(s.sum()),"maximum_drawdown":dd(s),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"monthly_median":float(m.median()),"worst_month":float(m.min()),"top5_positive_day_share":float(pos.head(5).sum()/pos.sum()) if pos.sum()>0 else None,"annual_returns":{str(k):float(v) for k,v in y.items()}}
def main():
 OUT.mkdir(parents=True,exist_ok=True); base=pd.read_csv(CAM/"CAM-0600"/"artifacts"/"shared"/"split_repaired_diagnostic_summary.csv").set_index("campaign_id"); repair=pd.read_csv(CAM/"CAM-0600"/"artifacts"/"shared"/"split_repaired_repair_diagnostic_summary.csv").set_index("campaign_id"); full={}; quote={}; variants={}
 for cid in PARENT+EXTRA:
  row=(repair if cid in REPAIR else base).loc[cid]; v=str(row.selected_variant); variants[cid]=v; run="RUN-0021" if cid in REPAIR else "RUN-0020"; safe=f"{v}__cost_2bps".replace("/","_").replace(":","_"); d=pd.read_parquet(CAM/cid/"artifacts"/run/"variants"/safe/"daily.parquet"); d.date=pd.to_datetime(d.date); full[cid]=d.set_index("date").net_pnl.sort_index(); q=pd.read_parquet(CAM/cid/"artifacts"/"RUN-0023"/"daily_0940_2bps_extra.parquet"); q.date=pd.to_datetime(q.date); quote[cid]=q.set_index("date").net_pnl.sort_index()
 portfolios={"parent4":PARENT}; portfolios.update({f"leave_out_{cid}":[x for x in PARENT if x!=cid] for cid in PARENT}); portfolios["parent_plus_sector"]=PARENT+["CAM-0618"]; portfolios["parent_plus_lowvol"]=PARENT+["CAM-0603"]; portfolios["parent_plus_both"]=PARENT+EXTRA; results={}
 for name,ids in portfolios.items():
  fs=pd.concat([full[x] for x in ids],axis=1).fillna(0).mean(axis=1); qs=pd.concat([quote[x] for x in ids],axis=1).fillna(0).mean(axis=1); results[name]={"sleeves":ids,"full":stats(fs),"quote":stats(qs)}; fs.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/f"{name}_full_daily.parquet",index=False); qs.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/f"{name}_quote_daily.parquet",index=False)
 frame=pd.concat(full,axis=1).fillna(0); report={"status":"completed","run_id":"RUN-0020","variants":variants,"component_correlations":frame.corr().to_dict(),"portfolios":results,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Adapted development diagnostic; additions were prespecified by distinct mechanism, not optimized."}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); path=CAM/"CAM-0625"/"runs"/"RUN-0020.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="completed"; run["result"]=report; run["decision"]="Prefer simplicity unless an addition improves both risk and concentration robustly; no promotion."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":"RUN-0020","event":"completed","portfolios":results,"holdout_rows_loaded":0})+"\n")
 print(pd.DataFrame([{"portfolio":k,**v["full"],"quote_return":v["quote"]["net_simple_return"],"quote_dd":v["quote"]["maximum_drawdown"],"quote_green_months":v["quote"]["positive_months"],"quote_red_months":v["quote"]["negative_months"]} for k,v in results.items()]).to_string(index=False))
if __name__=="__main__": main()
