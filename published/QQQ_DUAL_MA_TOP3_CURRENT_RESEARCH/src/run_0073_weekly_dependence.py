from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0073"


def weekly_series():
    fixed=pd.read_parquet(ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0028"/"quote_daily_control_f126_s21_2bps.parquet")
    fixed.date=pd.to_datetime(fixed.date);fixed=fixed.set_index("date").net_pnl.resample("W-FRI").sum()
    comp=pd.read_parquet(ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0058"/"daily_change_only_reserve0.005_2bps.parquet")
    comp.date=pd.to_datetime(comp.date);comp=comp.set_index("date")
    friday=comp.equity.resample("W-FRI").last().dropna();compound=friday.pct_change()
    first_active=comp.index[comp.gross_value>1e-12].min()
    fixed=fixed[fixed.index>=first_active].dropna();compound=compound[compound.index>=first_active].dropna()
    common=fixed.index.intersection(compound.index)
    return {"fixed":fixed.loc[common],"compound":compound.loc[common]},comp


def condition(rows,name,mask,next_ret):
    z=next_ret[mask & next_ret.notna()]
    rows.append({"condition":name,"n":int(len(z)),"positive":int((z>0).sum()),
                 "win_rate":float((z>0).mean()) if len(z) else None,
                 "mean_next":float(z.mean()) if len(z) else None,"median_next":float(z.median()) if len(z) else None,
                 "worst_next":float(z.min()) if len(z) else None,"best_next":float(z.max()) if len(z) else None})


def analyze(name,r,comp_daily):
    next_ret=r.shift(-1);rows=[]
    condition(rows,"unconditional",pd.Series(True,index=r.index),next_ret)
    condition(rows,"previous_positive",r>0,next_ret);condition(rows,"previous_negative",r<0,next_ret)
    condition(rows,"previous_two_positive",(r>0)&(r.shift(1)>0),next_ret)
    condition(rows,"previous_two_negative",(r<0)&(r.shift(1)<0),next_ret)
    for t in (.02,.05,.10,.15,.20):
        condition(rows,f"previous_ge_{int(t*100)}pct",r>=t,next_ret)
        condition(rows,f"previous_le_minus_{int(t*100)}pct",r<=-t,next_ret)
    four=(1+r).rolling(4).apply(np.prod,raw=True)-1 if name=="compound" else r.rolling(4).sum()
    q=pd.qcut(four.rank(method="first"),5,labels=False,duplicates="drop")
    for i in range(5): condition(rows,f"prior4_return_quintile_{i+1}",q.eq(i),next_ret)
    vol=r.rolling(4).std();vq=pd.qcut(vol.rank(method="first"),5,labels=False,duplicates="drop")
    for i in range(5): condition(rows,f"prior4_vol_quintile_{i+1}",vq.eq(i),next_ret)
    if name=="compound":
        rebalanced=comp_daily.rebalanced.resample("W-FRI").max().reindex(r.index).fillna(False).astype(bool)
        condition(rows,"previous_week_rebalanced",rebalanced,next_ret)
        condition(rows,"previous_week_not_rebalanced",~rebalanced,next_ret)
        condition(rows,"previous_10_to_15pct",(r>=.10)&(r<.15),next_ret)
        condition(rows,"previous_rebalanced_and_ge_10pct",rebalanced&(r>=.10),next_ret)
    table=pd.DataFrame(rows);table.insert(0,"series",name)
    autocorr={f"lag{i}":float(r.autocorr(i)) for i in range(1,5)}
    prev_loss=r<0;next_loss=next_ret<0;valid=next_ret.notna()
    a=int((prev_loss&next_loss&valid).sum());b=int((prev_loss&~next_loss&valid).sum())
    c=int((~prev_loss&next_loss&valid).sum());d=int((~prev_loss&~next_loss&valid).sum())
    odds,p=fisher_exact([[a,b],[c,d]])
    transitions={"loss_after_loss":a,"gain_after_loss":b,"loss_after_nonloss":c,"gain_after_nonloss":d,
                 "p_loss_after_loss":a/max(1,a+b),"p_loss_after_nonloss":c/max(1,c+d),"fisher_odds":float(odds),"fisher_p":float(p)}
    return table,{"autocorrelation":autocorr,"transitions":transitions,"weeks":len(r)}


def rebalance_stability(r,comp_daily):
    rebalanced=comp_daily.rebalanced.resample("W-FRI").max().reindex(r.index).fillna(False).astype(bool)
    x=pd.DataFrame({"return":r,"previous_week_rebalanced":rebalanced.shift(1)}).dropna()
    x.previous_week_rebalanced=x.previous_week_rebalanced.astype(bool)
    rows=[]
    for year,g in x.groupby(x.index.year):
        for state in (True,False):
            z=g.loc[g.previous_week_rebalanced.eq(state),"return"]
            rows.append({"period":str(year),"previous_week_rebalanced":state,"n":len(z),
                         "mean_return":z.mean(),"win_rate":(z>0).mean()})
    for label,g in (("2020_2022",x[x.index<"2023-01-01"]),("2023_2026",x[x.index>="2023-01-01"])):
        for state in (True,False):
            z=g.loc[g.previous_week_rebalanced.eq(state),"return"]
            rows.append({"period":label,"previous_week_rebalanced":state,"n":len(z),
                         "mean_return":z.mean(),"win_rate":(z>0).mean()})
    scale=[]
    for s in (0,.25,.5,.75,1):
        rr=x["return"]*np.where(x.previous_week_rebalanced,s,1)
        eq=(1+rr).cumprod();dd=eq/eq.cummax()-1
        scale.append({"scale_after_rebalance":s,"additive_weekly_return":rr.sum(),
                      "compounded_weekly_return":eq.iloc[-1]-1,"maximum_drawdown":-dd.min()})
    return pd.DataFrame(rows),pd.DataFrame(scale)


def current_week():
    dev=pd.read_parquet(ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0058"/"daily_change_only_reserve0.005_2bps.parquet")[["date","equity"]]
    oos=pd.read_parquet(ROOT/"campaigns"/"CAM-0611"/"artifacts"/"RUN-0059"/"oos_daily.parquet")[["date","equity"]]
    x=pd.concat([dev,oos]).drop_duplicates("date",keep="last");x.date=pd.to_datetime(x.date);x=x.set_index("date").equity.sort_index()
    w=x.resample("W-FRI").last().pct_change()
    return {"week_ending":str(w.index[-1].date()),"return":float(w.iloc[-1]),"prior_week_return":float(w.iloc[-2]),
            "prior4_compounded_return":float((1+w.iloc[-4:]).prod()-1)}


def main():
    OUT.mkdir(parents=True,exist_ok=True);series,comp_daily=weekly_series();tables=[];stats={}
    for name,r in series.items():
        table,stat=analyze(name,r,comp_daily);tables.append(table);stats[name]=stat
        pd.DataFrame({"week":r.index,"return":r.values}).to_csv(OUT/f"weekly_{name}.csv",index=False)
    conditions=pd.concat(tables,ignore_index=True);conditions.to_csv(OUT/"conditional_results.csv",index=False)
    stability,scales=rebalance_stability(series["compound"],comp_daily)
    stability.to_csv(OUT/"rebalance_state_stability.csv",index=False)
    scales.to_csv(OUT/"rebalance_scale_diagnostic.csv",index=False)
    report={"status":"completed","maximum_inference_date":"2026-04-30","holdout_rows_loaded_in_inference":0,
            "stats":stats,"current_observed_descriptive":current_week()}
    (OUT/"report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2));print(conditions[conditions.condition.str.contains("unconditional|previous_positive|previous_negative|previous_two|previous_ge_|previous_le_minus")].to_string(index=False))


if __name__=="__main__":main()
