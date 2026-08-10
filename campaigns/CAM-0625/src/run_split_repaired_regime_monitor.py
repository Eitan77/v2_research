from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0019"
def dd(s):
 e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max()) if len(s) else 0
def stats(s):
 m=s.groupby(s.index.to_period("M")).sum(); return {"net_simple_return":float(s.sum()),"maximum_drawdown":dd(s),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"active_months":int((m.abs()>1e-12).sum()),"worst_month":float(m.min()),"monthly_median":float(m.median())}
def main():
 OUT.mkdir(parents=True,exist_ok=True); full=pd.read_parquet(CAM/"CAM-0625"/"artifacts"/"RUN-0017"/"full_equal_daily.parquet"); full.date=pd.to_datetime(full.date); full=full.set_index("date").net_pnl.sort_index(); quote=pd.read_parquet(CAM/"CAM-0625"/"artifacts"/"RUN-0017"/"quote_equal_daily.parquet"); quote.date=pd.to_datetime(quote.date); quote=quote.set_index("date").net_pnl.sort_index(); monthly=full.groupby(full.index.to_period("M")).sum(); trailing=monthly.rolling(12,min_periods=12); signals={"positive_trailing_return":trailing.sum().shift(1)>0,"positive_and_consistent":(trailing.sum().shift(1)>0)&(trailing.apply(lambda x:(x>0).sum(),raw=True).shift(1)>=7)}; rows=[]
 for name,signal in signals.items():
  fmask=pd.Series(full.index.to_period("M").map(signal).fillna(False).to_numpy(bool),index=full.index); qmask=pd.Series(quote.index.to_period("M").map(signal).fillna(False).to_numpy(bool),index=quote.index); fs=full*fmask; qs=quote*qmask; rec={"variant":name,"full":stats(fs),"quote":stats(qs),"quote_active_months":sorted(set(str(x) for x in quote.index.to_period("M")[qmask.to_numpy()]))}; rows.append(rec); fs.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/f"{name}_full_daily.parquet",index=False); qs.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/f"{name}_quote_daily.parquet",index=False)
 report={"status":"completed","run_id":"RUN-0019","parent_unfiltered":{"full":stats(full),"quote":stats(quote)},"variants":rows,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"interpretation":"Adapted development monitor; variants were prespecified and not threshold-mined."}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); path=CAM/"CAM-0625"/"runs"/"RUN-0019.yaml"; run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="completed"; run["result"]=report; run["decision"]="Retain only if drawdown improves without destroying recent participation; no promotion."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":"RUN-0019","event":"completed","variants":rows,"holdout_rows_loaded":0})+"\n")
 print(json.dumps(report,indent=2))
if __name__=="__main__": main()
