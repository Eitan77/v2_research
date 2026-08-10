from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import yaml
from run_ensemble import paths

ROOT=Path(__file__).resolve().parents[3]; CAM=ROOT/"campaigns"; OUT=CAM/"CAM-0625"/"artifacts"/"RUN-0006"

def gate(s,n,k):
 m=s.groupby(s.index.to_period("M")).sum(); decision=(m.rolling(n,min_periods=n).sum()>0)&((m>0).rolling(n,min_periods=n).sum()>=k); active=decision.shift(1).fillna(False); return pd.Series(s.index.to_period("M").map(active).fillna(False).to_numpy(bool),index=s.index)

def calc(s,g,label,sample):
 changes=g.astype(int).diff().abs().fillna(g.astype(int))*2/10000; x=s.where(g,0)-changes; e=1+x.cumsum(); m=x.groupby(x.index.to_period("M")).sum(); return x,{"sample":sample,"rule":label,"net_simple_return":float(x.sum()),"maximum_drawdown":float(((e.cummax()-e)/e.cummax()).max()),"active_days":int(g.sum()),"active_fraction":float(g.mean()),"gate_changes":int((g.astype(int).diff().abs()>0).sum()),"positive_months":int((m>0).sum()),"negative_months":int((m<0).sum()),"inactive_months":int((m.abs()<1e-12).sum()),"monthly_average":float(m.mean()),"worst_month":float(m.min()),"best_month":float(m.max())}

def main():
 OUT.mkdir(parents=True,exist_ok=True); full=paths(False).mean(axis=1); quote=paths(True).mean(axis=1); rows=[]
 rules=(("responsive_6m",6,4),("cautious_12m",12,8))
 for label,n,k in rules:
  g=gate(full,n,k); x,r=calc(full,g,label,"full_history"); rows.append(r); x.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/f"daily_full_{label}.parquet",index=False)
  # Apply the causal full-history gate to the quote dates so no quote-window look-ahead initializes the monitor.
  qg=g.reindex(quote.index).ffill().fillna(False); qx,qr=calc(quote,qg,label,"quote_0940_2bps_extra"); rows.append(qr); qx.rename("net_pnl").rename_axis("date").reset_index().to_parquet(OUT/f"daily_quote_{label}.parquet",index=False)
 frame=pd.DataFrame(rows); frame.to_csv(OUT/"regime_monitor_metrics.csv",index=False); report={"status":"completed","run_id":"RUN-0006","metrics":json.loads(frame.to_json(orient="records")),"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}; (OUT/"execution_report.json").write_text(json.dumps(report,indent=2)+"\n")
 run=CAM/"CAM-0625"/"runs"/"RUN-0006.yaml"; y=yaml.safe_load(run.read_text()); y["status"]="completed"; y["result"]=report; y["decision"]="Use only as adapted operational monitoring evidence; compare opportunity cost and false shutdowns before freezing a forward rule."; run.write_text(yaml.safe_dump(y,sort_keys=False),encoding="utf-8")
 with (CAM/"CAM-0625"/"WORKLOG.jsonl").open("a") as f: f.write(json.dumps({"ts":pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),"run_id":"RUN-0006","event":"completed","holdout_rows_loaded":0})+"\n")
 print(frame.to_string(index=False))
if __name__=="__main__": main()
