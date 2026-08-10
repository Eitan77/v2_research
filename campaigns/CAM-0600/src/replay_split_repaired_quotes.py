from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

from suite_core import CAMPAIGNS

SHARED=CAMPAIGNS/"CAM-0600"/"artifacts"/"shared"
REPAIR_IDS={"CAM-0608","CAM-0609","CAM-0617","CAM-0621"}
EXTRA=(0.0,2.0,5.0,10.0)
RUN_ID="RUN-0023"

def max_dd(net):
 equity=1+net.cumsum(); return float(((equity.cummax()-equity)/equity.cummax()).max()) if len(net) else 0.0

def quote_sources(label):
 names = (["target_change_matches_0930_1s.parquet","target_change_matches_0930_5s.parquet","repair_target_change_matches_0930_5s.parquet","target_change_matches_0930_30s.parquet","target_change_matches_0930_120s.parquet","split_remote_matches_0930_5s.parquet","split_repair_remote_matches_0930_5s.parquet","split_remote_matches_0930_30s.parquet","split_remote_matches_0930_120s.parquet"] if label=="0930" else ["target_change_matches_0940_5s.parquet","target_change_matches_0940_30s.parquet","repair_target_change_matches_0940_30s.parquet","target_change_matches_0940_120s.parquet","repair_target_change_matches_0940_120s.parquet","split_matches_0940_5m.parquet","split_repair_matches_0940_5m.parquet","split_remote_matches_0940_5s.parquet","split_remote_matches_0940_30s.parquet","split_remote_matches_0940_120s.parquet"])
 frames=[]
 for priority,name in enumerate(names):
  path=SHARED/name
  if path.exists():
   x=pd.read_parquet(path); x["priority"]=priority; frames.append(x)
 q=pd.concat(frames,ignore_index=True); q["target_ts"]=pd.to_datetime(q.target_ts,utc=True); q["quote_ts"]=pd.to_datetime(q.quote_ts,utc=True)
 valid=q.bid_price.notna()&q.ask_price.notna()&(q.bid_price>0)&(q.ask_price>=q.bid_price)
 return q[valid].sort_values("priority").drop_duplicates(["symbol","target_ts","role"],keep="first")

def ledger(label):
 d=pd.concat([pd.read_parquet(SHARED/f"split_target_change_trades_{label}.parquet"),pd.read_parquet(SHARED/f"split_repair_target_change_trades_{label}.parquet")],ignore_index=True); d["target_ts"]=pd.to_datetime(d.target_ts,utc=True); return d

def freeze(cid,variant):
 path=CAMPAIGNS/cid/"runs"/f"{RUN_ID}.yaml"
 if not path.exists():
  parent="RUN-0021" if cid in REPAIR_IDS else "RUN-0020"
  payload={"run_id":RUN_ID,"campaign_id":cid,"parent_run":"RUN-0022","status":"planned","change":"Repaired target-change SIP replay with valid-quote remainder accounting.","reason":"RUN-0022 counted null-quote rows as matched keys.","expected_effect":"Restore honest 09:40 role coverage without changing signal or fill rules.","frozen_contract":"campaigns/CAM-0600/SPLIT_REPAIRED_QUOTE_REPAIR_CONTRACT.yaml","configuration":{"window_start":"2025-05-01","window_end":"2026-04-30","clocks":["09:30","09:40"],"additional_slippage_bps_per_side":list(EXTRA),"selected_variant":variant,"holdout_access":False},"result":None,"decision":None}
  path.write_text(yaml.safe_dump(payload,sort_keys=False),encoding="utf-8")
  with (CAMPAIGNS/cid/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":RUN_ID,"event":"planned","selected_variant":variant,"holdout_rows_loaded":0})+"\n")
 return path

def main():
 ledgers={label:ledger(label) for label in ("0930","0940")}; quotes={label:quote_sources(label) for label in ("0930","0940")}
 replays={}
 for label in ("0930","0940"):
  replays[label]=ledgers[label].merge(quotes[label][["symbol","target_ts","role","quote_ts","bid_price","ask_price","bid_size","ask_size"]],on=["symbol","target_ts","role"],how="left",validate="many_to_one")
  replays[label]["quote_complete"]=replays[label].bid_price.notna()&replays[label].ask_price.notna()&(replays[label].bid_price>0)&(replays[label].ask_price>=replays[label].bid_price)
 ref=replays["0930"][replays["0930"].role!="exit_bid_before"].copy(); ref["reference_mid"]=(ref.bid_price+ref.ask_price)/2; ref=ref[["campaign_id","session_date","symbol","side","reference_mid"]].drop_duplicates(["campaign_id","session_date","symbol","side"])
 replays["0940"]=replays["0940"].merge(ref,on=["campaign_id","session_date","symbol","side"],how="left",validate="many_to_one")
 replays["0930"]["reference_mid"]=(replays["0930"].bid_price+replays["0930"].ask_price)/2
 rows=[]
 for label,replay in replays.items():
  replay["effective_complete"]=replay.quote_complete & (replay.role.eq("exit_bid_before") | (replay.reference_mid.notna()&(replay.reference_mid>0)))
  replay.to_parquet(SHARED/f"split_repaired_quote_replay_{label}.parquet",index=False)
  for cid,g in replay.groupby("campaign_id",sort=True):
   variant=str(g.variant_id.iloc[0]); path=freeze(str(cid),variant); out=CAMPAIGNS/cid/"artifacts"/RUN_ID; out.mkdir(parents=True,exist_ok=True); local=[]
   for extra in EXTRA:
    if str(g.holding.iloc[0])=="open_to_close":
     complete=g[g.quote_complete]; buy=complete[complete.side=="buy"][["session_date","symbol","delta_weight","ask_price"]].rename(columns={"ask_price":"entry"}); sell=complete[complete.side=="sell"][["session_date","symbol","bid_price"]].rename(columns={"bid_price":"exit"}); pairs=buy.merge(sell,on=["session_date","symbol"],how="inner",validate="one_to_one"); pairs["pnl"]=pairs.delta_weight*(pairs.exit/pairs.entry-1-2*extra/10000); daily=pairs.groupby(pd.to_datetime(pairs.session_date)).pnl.sum().sort_index(); coverage=float(len(pairs)*2/len(g))
    else:
     complete=g[g.effective_complete].copy(); buy=complete.side.eq("buy"); complete["execution_adjustment"]=np.where(buy,complete.delta_weight*(complete.ask_price/complete.reference_mid-1),complete.delta_weight*(1-complete.bid_price/complete.reference_mid))+complete.delta_weight*extra/10000
     parent="RUN-0021" if cid in REPAIR_IDS else "RUN-0020"; safe=f"{variant}__cost_2bps".replace("/","_").replace(":","_"); bar=pd.read_parquet(CAMPAIGNS/cid/"artifacts"/parent/"variants"/safe/"daily.parquet"); bar["date"]=pd.to_datetime(bar.date); bar=bar[(bar.date>=pd.Timestamp("2025-05-01"))&(bar.date<=pd.Timestamp("2026-04-30"))].set_index("date"); adjustment=complete.groupby(pd.to_datetime(complete.session_date)).execution_adjustment.sum(); daily=bar.gross_pnl.subtract(adjustment,fill_value=0).sort_index(); coverage=float(g.effective_complete.mean())
    monthly=daily.groupby(daily.index.to_period("M")).sum(); rec={"campaign_id":cid,"run_id":RUN_ID,"variant_id":variant,"clock":label,"extra_slippage_bps_per_side":extra,"net_simple_return":float(daily.sum()),"maximum_drawdown":max_dd(daily),"trade_roles":len(g),"role_coverage":coverage,"active_sessions":int((daily.abs()>1e-12).sum()),"green_sessions":int((daily>1e-12).sum()),"red_sessions":int((daily<-1e-12).sum()),"positive_months":int((monthly>1e-12).sum()),"negative_months":int((monthly<-1e-12).sum()),"monthly_average":float(monthly.mean()),"monthly_median":float(monthly.median()),"worst_month":float(monthly.min()),"best_month":float(monthly.max()),"reference_price":"09:30 SIP midpoint","holdout_rows_loaded":0,"broker_margin":False,"direct_short":False}; rows.append(rec); local.append(rec); daily.rename("net_pnl").rename_axis("date").reset_index().to_parquet(out/f"daily_{label}_{extra:g}bps_extra.parquet",index=False)
   pd.DataFrame(local).to_csv(out/f"quote_metrics_{label}.csv",index=False)
   run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="completed"; run["result"]={"metrics":local,"maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0}; run["decision"]="Development quote evidence only; requires repaired cross-family and pseudo-OOS audit."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
  print(label,pd.DataFrame([r for r in rows if r["clock"]==label and r["extra_slippage_bps_per_side"]==2])[["campaign_id","net_simple_return","maximum_drawdown","role_coverage","positive_months","negative_months"]].to_string(index=False))
 pd.DataFrame(rows).to_csv(SHARED/"split_repaired_quote_metrics_RUN-0023.csv",index=False)

if __name__=="__main__": main()
