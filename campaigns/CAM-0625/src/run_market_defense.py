from __future__ import annotations
import json,sys
from pathlib import Path
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; sys.path.insert(0,str(CAM/"CAM-0600"/"src"))
from run_ensemble import paths,invvol_weights
from suite_core import load_panels

OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0011"
def dd(s):
 e=1+s.cumsum(); return float(((e.cummax()-e)/e.cummax()).max())
def metric(sample,rule,window,s,g):
 m=s.groupby(s.index.to_period("M")).sum(); return {"sample":sample,"rule":rule,"SPY_SMA_window":window,"net_simple_return":float(s.sum()),"maximum_drawdown":dd(s),"active_fraction":float(g.mean()),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"inactive_months":int((m.abs()<1e-12).sum()),"monthly_average":float(m.mean()),"worst_month":float(m.min()),"best_month":float(m.max())}
def main():
 OUT.mkdir(parents=True,exist_ok=True); z=paths(False); q=paths(True); w=invvol_weights(z); bases={"equal":z.mean(axis=1),"causal_inverse_vol":(z*w).sum(axis=1)-w.diff().abs().sum(axis=1).fillna(0)*2/10000}; qw=invvol_weights(q); qbases={"equal":q.mean(axis=1),"causal_inverse_vol":(q*qw).sum(axis=1)-qw.diff().abs().sum(axis=1).fillna(0)*2/10000}; p=load_panels()["etf"]; spy=pd.Series(p.adj_close[:,p.symbol_to_col['SPY']],index=p.dates); rows=[]
 for window in (100,150,200):
  # At open t, only close and SMA through t-1 are available.
  gate=(spy.shift(1)>spy.rolling(window,min_periods=window).mean().shift(1))
  for rule,base in bases.items():
   g=gate.reindex(base.index).fillna(False); cost=g.astype(int).diff().abs().fillna(g.astype(int))*2/10000; s=base.where(g,0)-cost; rows.append(metric('full_history',rule,window,s,g)); s.rename('net_pnl').rename_axis('date').reset_index().to_parquet(OUT/f'daily_full_{rule}_ma{window}.parquet',index=False)
  for rule,base in qbases.items():
   g=gate.reindex(base.index).fillna(False); cost=g.astype(int).diff().abs().fillna(g.astype(int))*2/10000; s=base.where(g,0)-cost; rows.append(metric('quote_0940_2bps_extra',rule,window,s,g)); s.rename('net_pnl').rename_axis('date').reset_index().to_parquet(OUT/f'daily_quote_{rule}_ma{window}.parquet',index=False)
 frame=pd.DataFrame(rows); frame.to_csv(OUT/'market_defense_metrics.csv',index=False); report={"status":"completed","run_id":"RUN-0011","metrics":json.loads(frame.to_json(orient='records')),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}; (OUT/'execution_report.json').write_text(json.dumps(report,indent=2)+'\n'); run=CAM/'CAM-0625'/'runs'/'RUN-0011.yaml'; y=yaml.safe_load(run.read_text()); y['status']='completed'; y['result']=report; y['decision']='Retain only if drawdown reduction is broad across MA windows and recent income remains economically useful; no promotion.'; run.write_text(yaml.safe_dump(y,sort_keys=False),encoding='utf-8')
 with (CAM/'CAM-0625'/'WORKLOG.jsonl').open('a') as f: f.write(json.dumps({"ts":pd.Timestamp.now(tz='America/Los_Angeles').isoformat(),"run_id":"RUN-0011","event":"completed","holdout_rows_loaded":0})+'\n')
 print(frame.to_string(index=False))
if __name__=='__main__': main()
