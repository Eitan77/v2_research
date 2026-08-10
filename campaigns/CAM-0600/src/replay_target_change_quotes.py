from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from suite_core import CAMPAIGNS, write_json


SHARED=CAMPAIGNS/"CAM-0600"/"artifacts"/"shared"
REPAIR_IDS={"CAM-0608","CAM-0609","CAM-0617","CAM-0621"}
EXTRA_BPS=(0.0,2.0,5.0,10.0)


def max_drawdown(net):
    equity=1.0+net.cumsum(); peak=equity.cummax(); return float(((peak-equity)/peak).max()) if len(net) else 0.0


def freeze_run(campaign_id,variant_id):
    run_id="RUN-0011" if campaign_id in REPAIR_IDS else "RUN-0009"
    path=CAMPAIGNS/campaign_id/"runs"/f"{run_id}.yaml"
    if path.exists(): return run_id
    parent="RUN-0010" if campaign_id in REPAIR_IDS else "RUN-0008"
    payload={"run_id":run_id,"campaign_id":campaign_id,"parent_run":parent,"status":"planned","change":"Exact marketable SIP replay at causal target-weight changes at 09:30 and 09:40.","reason":"Correct the earlier conservative daily-reset quote stress for multi-day holdings while retaining realistic spreads and delay.","expected_effect":"Determine whether the structured development survivor remains profitable when only actual target changes pay marketable execution.","frozen_contract":"campaigns/CAM-0600/TARGET_CHANGE_QUOTE_CONTRACT.yaml","configuration":{"window_start":"2025-05-01","window_end":"2026-04-30","clocks":["09:30","09:40"],"additional_slippage_bps_per_side":[0,2,5,10],"passive_fill_credit":False,"holdout_access":False,"selected_variant":variant_id},"result":None,"decision":None}
    path.write_text(yaml.safe_dump(payload,sort_keys=False),encoding="utf-8"); return run_id


def load_inputs(label):
    ledger=pd.concat([pd.read_parquet(SHARED/f"target_change_trades_{label}.parquet"),pd.read_parquet(SHARED/f"repair_target_change_trades_{label}.parquet")],ignore_index=True)
    specs=(
        [(SHARED/"target_change_matches_0930_1s.parquet",1),(SHARED/"target_change_matches_0930_5s.parquet",5),(SHARED/"repair_target_change_matches_0930_5s.parquet",5),(SHARED/"target_change_matches_0930_30s.parquet",30),(SHARED/"target_change_matches_0930_120s.parquet",120)]
        if label=="0930" else
        [(SHARED/"target_change_matches_0940_5s.parquet",5),(SHARED/"target_change_matches_0940_30s.parquet",30),(SHARED/"repair_target_change_matches_0940_30s.parquet",30),(SHARED/"target_change_matches_0940_120s.parquet",120),(SHARED/"repair_target_change_matches_0940_120s.parquet",120)]
    )
    matches=[]
    for path,window in specs:
        if path.exists():
            x=pd.read_parquet(path); x["window_seconds"]=window; matches.append(x)
    quotes=pd.concat(matches,ignore_index=True).sort_values("window_seconds").drop_duplicates(["symbol","target_ts","role"],keep="first")
    ledger["target_ts"]=pd.to_datetime(ledger.target_ts,utc=True); quotes["target_ts"]=pd.to_datetime(quotes.target_ts,utc=True)
    replay=ledger.merge(quotes[["symbol","target_ts","role","quote_ts","bid_price","ask_price","bid_size","ask_size","window_seconds"]],on=["symbol","target_ts","role"],how="left",validate="many_to_one")
    replay["quote_complete"]=replay.bid_price.notna()&replay.ask_price.notna()&(replay.bid_price>0)&(replay.ask_price>=replay.bid_price)
    return replay


def multiday_metrics(campaign_id,group,extra,run_id):
    complete=group[group.effective_complete].copy()
    buy=complete.side.eq("buy")
    complete["execution_adjustment"]=np.where(buy,complete.delta_weight*(complete.ask_price/complete.reference_mid-1),complete.delta_weight*(1-complete.bid_price/complete.reference_mid))
    complete["execution_adjustment"] += complete.delta_weight*extra/10000.0
    daily_path=CAMPAIGNS/campaign_id/"artifacts"/("RUN-0010" if campaign_id in REPAIR_IDS else "RUN-0008")/"variants"/(str(group.variant_id.iloc[0])+"__cost_2bps").replace("/","_").replace(":","_")/"daily.parquet"
    bar=pd.read_parquet(daily_path); bar["date"]=pd.to_datetime(bar.date); bar=bar[(bar.date>=pd.Timestamp("2025-05-01"))&(bar.date<=pd.Timestamp("2026-04-30"))].set_index("date")
    adjustment=complete.groupby(pd.to_datetime(complete.session_date))["execution_adjustment"].sum()
    daily=bar.gross_pnl.subtract(adjustment,fill_value=0.0).sort_index()
    return daily,complete


def intraday_metrics(group,extra):
    complete=group[group.quote_complete].copy()
    buy=complete[complete.side=="buy"][["campaign_id","session_date","symbol","delta_weight","ask_price"]].rename(columns={"ask_price":"entry_ask"})
    sell=complete[complete.side=="sell"][["campaign_id","session_date","symbol","bid_price"]].rename(columns={"bid_price":"exit_bid"})
    pairs=buy.merge(sell,on=["campaign_id","session_date","symbol"],how="inner",validate="one_to_one")
    pairs["pnl"]=pairs.delta_weight*(pairs.exit_bid/pairs.entry_ask-1-2*extra/10000.0)
    daily=pairs.groupby(pd.to_datetime(pairs.session_date)).pnl.sum().sort_index()
    return daily,pairs


def main():
    all_rows=[]
    for label in ("0930","0940"):
        replay=load_inputs(label)
        if label=="0930":
            replay["reference_mid"]=(replay.bid_price+replay.ask_price)/2
        else:
            reference=pd.read_parquet(SHARED/"target_change_replay_0930.parquet")
            reference=reference[reference.role!="exit_bid_before"].copy()
            reference["reference_mid"]=(reference.bid_price+reference.ask_price)/2
            reference=reference[["campaign_id","session_date","symbol","side","reference_mid"]].drop_duplicates(["campaign_id","session_date","symbol","side"])
            replay=replay.merge(reference,on=["campaign_id","session_date","symbol","side"],how="left",validate="many_to_one")
        replay["effective_complete"]=replay.quote_complete & replay.reference_mid.notna() & (replay.reference_mid>0)
        replay.to_parquet(SHARED/f"target_change_replay_{label}.parquet",index=False)
        for campaign_id,group in replay.groupby("campaign_id",sort=True):
            run_id=freeze_run(str(campaign_id),str(group.variant_id.iloc[0])); rows=[]
            out=CAMPAIGNS/campaign_id/"artifacts"/run_id; out.mkdir(parents=True,exist_ok=True)
            for extra in EXTRA_BPS:
                if str(group.holding.iloc[0])=="open_to_close": daily,detail=intraday_metrics(group,extra)
                else: daily,detail=multiday_metrics(str(campaign_id),group,extra,run_id)
                monthly=daily.groupby(daily.index.to_period("M")).sum(); complete_roles=int(group.quote_complete.sum())
                role_coverage=float(group.quote_complete.mean()) if str(group.holding.iloc[0])=="open_to_close" else float(group.effective_complete.mean())
                record={"campaign_id":campaign_id,"run_id":run_id,"variant_id":str(group.variant_id.iloc[0]),"clock":label,"extra_slippage_bps_per_side":extra,"net_simple_return":float(daily.sum()),"maximum_drawdown":max_drawdown(daily),"trade_roles":int(len(group)),"complete_trade_roles":complete_roles,"role_coverage":role_coverage,"active_sessions":int((daily.abs()>1e-12).sum()),"green_sessions":int((daily>1e-12).sum()),"red_sessions":int((daily<-1e-12).sum()),"positive_months":int((monthly>1e-12).sum()),"negative_months":int((monthly<-1e-12).sum()),"monthly_average":float(monthly.mean()),"monthly_median":float(monthly.median()),"worst_month":float(monthly.min()),"best_month":float(monthly.max()),"mean_spread_bps":float(((group.ask_price-group.bid_price)/((group.ask_price+group.bid_price)/2)*10000).mean()),"reference_price":"09:30 SIP midpoint","holdout_rows_loaded":0,"broker_margin":False,"direct_short":False}
                rows.append(record); all_rows.append(record)
                if extra in EXTRA_BPS:
                    suffix=f"{extra:g}bps_extra"
                    daily.rename("net_pnl").rename_axis("date").reset_index().to_parquet(
                        CAMPAIGNS/campaign_id/"artifacts"/run_id/f"daily_{label}_{suffix}.parquet",
                        index=False,
                    )
            frame_path=out/"target_change_quote_metrics.csv"; existing=pd.read_csv(frame_path) if frame_path.exists() else pd.DataFrame(); combined=pd.concat([existing[existing.clock.astype(str).str.zfill(4)!=label] if len(existing) else existing,pd.DataFrame(rows)],ignore_index=True); combined.to_csv(frame_path,index=False)
        print(label,pd.DataFrame([x for x in all_rows if x["clock"]==label and x["extra_slippage_bps_per_side"]==2])[['campaign_id','net_simple_return','maximum_drawdown','role_coverage','positive_months','negative_months']].to_string(index=False))
    pd.DataFrame(all_rows).to_csv(SHARED/"target_change_quote_metrics.csv",index=False)


if __name__=="__main__": main()
