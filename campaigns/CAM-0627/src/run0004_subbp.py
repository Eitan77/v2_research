from __future__ import annotations
import importlib.util,json
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[3];CAM=ROOT/'campaigns'/'CAM-0627';OUT=CAM/'artifacts'/'RUN-0004'
spec=importlib.util.spec_from_file_location('r3',CAM/'src'/'run0003_sync_and_replay.py');r3=importlib.util.module_from_spec(spec);spec.loader.exec_module(r3)
def main():
 OUT.mkdir(parents=True,exist_ok=True);data=pd.read_parquet(CAM/'artifacts'/'RUN-0003'/'synchronized_50ms.parquet');rows=[]
 for age in (50,100,250):
  for anchor in (30,60):
   for threshold in (0,0.05,0.1,0.25,0.5):
    for hold in (1,5,30,120):
     for stop in (5,10,20):
      trades=[]
      for _,window in data.groupby(['date','window'],sort=False):trades.extend(r3.simulate_window(window.reset_index(drop=True),age,anchor,threshold,hold,stop))
      trades=pd.DataFrame(trades)
      for bps in (-1,0,1,2):
       m=r3.metrics(trades,bps);m.update({'age_ms':age,'anchor_seconds':anchor,'threshold_bps':threshold,'hold_seconds':hold,'stop_bps':stop,'additional_bps_per_side':bps});rows.append(m)
 grid=pd.DataFrame(rows);grid.to_parquet(OUT/'grid.parquet',index=False);best={}
 for bps in (-1,0,1,2):
  row=grid[grid.additional_bps_per_side.eq(bps)].sort_values('net_return',ascending=False).iloc[0];best[str(bps)]={k:(v.item() if hasattr(v,'item') else v) for k,v in row.items()}
 report={'status':'completed','planned_variants':360,'executed_variants':int(len(grid)/4),'best_by_cost':best,'maximum_loaded_date':str(pd.to_datetime(data.date).max().date()),'holdout_rows_loaded':0};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n');print(json.dumps(report,indent=2,default=str))
if __name__=='__main__':main()
