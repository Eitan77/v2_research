from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))
from run_0033_exit_overlays import base_context

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0071"
COST = 9.740340418 / 10000.0
RECENT = pd.Timestamp("2025-05-01")
THRESHOLDS = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40)


def weekly_signals(dates):
    periods = pd.DatetimeIndex(dates).to_period("W-FRI")
    return [i for i in range(len(dates)-1) if periods[i+1] != periods[i]]


def build_schedules(p, score, mask):
    schedules = {"control": {}}
    for t in THRESHOLDS:
        for mode in ("substitute", "entry_only", "cash"):
            schedules[f"{mode}_{int(t*100):02d}"] = {}
    active = {k: tuple() for k in schedules}
    counts = {k: {"blocked": 0, "substituted": 0, "cash_slots": 0} for k in schedules}
    for i in weekly_signals(p.dates):
        execution_i = i + 1
        candidates = np.flatnonzero(mask[i] & np.isfinite(score[i]))
        ranked = candidates[np.argsort(score[i, candidates], kind="stable")[::-1]]
        baseline = tuple(int(c) for c in ranked[:3])
        schedules["control"][execution_i] = (baseline, np.ones(len(baseline))/max(1, len(baseline)))
        for t in THRESHOLDS:
            hot = {int(c) for c in ranked if i >= 5 and np.isfinite(p.adj_close[i,c]) and np.isfinite(p.adj_close[i-5,c])
                   and p.adj_close[i,c] / p.adj_close[i-5,c] - 1.0 > t}
            key = f"substitute_{int(t*100):02d}"
            chosen = tuple(int(c) for c in ranked if int(c) not in hot)[:3]
            schedules[key][execution_i] = (chosen, np.ones(len(chosen))/max(1, len(chosen)))
            counts[key]["blocked"] += sum(int(c) in hot for c in baseline)
            counts[key]["substituted"] += len(set(chosen) - set(baseline))
            key = f"entry_only_{int(t*100):02d}"
            prior = active[key]
            chosen = tuple(int(c) for c in ranked if int(c) in prior or int(c) not in hot)[:3]
            schedules[key][execution_i] = (chosen, np.ones(len(chosen))/max(1, len(chosen)))
            counts[key]["blocked"] += sum(int(c) in hot and int(c) not in prior for c in baseline)
            counts[key]["substituted"] += len(set(chosen) - set(baseline))
            active[key] = chosen
            key = f"cash_{int(t*100):02d}"
            chosen = tuple(int(c) for c in baseline if int(c) not in hot)
            schedules[key][execution_i] = (chosen, np.ones(len(chosen))/3.0)
            blocked_slots = sum(int(c) in hot for c in baseline)
            counts[key]["blocked"] += blocked_slots
            counts[key]["cash_slots"] += blocked_slots
    return schedules, counts


def fixed_base(p, schedule):
    target = np.zeros_like(p.adj_close)
    current = np.zeros(p.adj_close.shape[1])
    for i in range(len(p.dates)):
        if i in schedule:
            chosen, weights = schedule[i]
            current = np.zeros_like(current)
            if len(chosen): current[list(chosen)] = weights
        target[i] = current
    pnl, turnover = np.zeros(len(p.dates)), np.zeros(len(p.dates))
    previous = np.zeros(p.adj_close.shape[1])
    for i in range(len(p.dates)):
        turnover[i] = np.abs(target[i]-previous).sum()
        day_return = p.open_to_close_return[i] if i == len(p.dates)-1 else p.open_to_next_open_return[i]
        pnl[i] = np.nansum(target[i]*np.nan_to_num(day_return, nan=0.0))-COST*turnover[i]
        previous = target[i].copy()
    equity = 1+np.cumsum(pnl)
    peaks = np.maximum.accumulate(np.r_[1.0,equity])[1:]
    dates = pd.DatetimeIndex(p.dates)
    monthly = pd.Series(pnl,index=dates).groupby(dates.to_period("M")).sum()
    recent = dates >= RECENT
    thirds = np.array_split(np.arange(len(p.dates)),3)
    return {"return":float(pnl.sum()),"maximum_drawdown":float(np.max((peaks-equity)/peaks)),
            "recent12_return":float(pnl[recent].sum()),"positive_months":int((monthly>0).sum()),
            "negative_months":int((monthly<0).sum()),"worst_month":float(monthly.min()),
            "turnover":float(turnover.sum()),"trade_sessions":int((turnover>1e-12).sum()),
            "chronology":[float(pnl[z].sum()) for z in thirds]}


def compounded(p, schedule):
    current = np.zeros(p.adj_close.shape[1]); cash=1.0; active=(tuple(),tuple())
    rows=[]; rebalances=0; min_cash=1.0; max_gross=0.0
    for i,day in enumerate(p.dates):
        if i in schedule:
            chosen, weights = schedule[i]
            signature=(tuple(chosen),tuple(np.round(weights,12)))
            if signature != active:
                nav=cash+current.sum(); w=np.zeros_like(current)
                if len(chosen): w[list(chosen)]=weights
                cash_target=nav*(1-(1-0.005)*w.sum())
                def ending(scale):
                    wanted=scale*w; delta=wanted-current
                    return cash-delta.sum()-COST*np.abs(delta).sum()
                lo,hi=0.0,nav
                for _ in range(100):
                    mid=(lo+hi)/2
                    if ending(mid)>=cash_target: lo=mid
                    else: hi=mid
                current=lo*w; cash=ending(lo); active=signature; rebalances+=1
        close_factor=np.divide(p.adj_close[i],p.adj_open[i],out=np.ones(p.n_symbols),
                               where=np.isfinite(p.adj_close[i])&np.isfinite(p.adj_open[i])&(p.adj_open[i]>0))
        close_values=current*close_factor
        equity=cash+close_values.sum(); gross=close_values.sum()
        min_cash=min(min_cash,cash); max_gross=max(max_gross,gross/equity if equity>0 else np.inf)
        rows.append((pd.Timestamp(day),equity))
        if i+1 < len(p.dates):
            dividend_adjusted=np.nan_to_num(p.dividend_grid[i+1]*p.split_factor[i+1],nan=0.0)
            next_factor=np.divide(p.adj_open[i+1]+dividend_adjusted,p.adj_open[i],out=np.ones(p.n_symbols),
                                  where=np.isfinite(p.adj_open[i+1])&np.isfinite(p.adj_open[i])&(p.adj_open[i]>0))
            current *= next_factor
    x=pd.DataFrame(rows,columns=["date","equity"]).set_index("date").equity
    dd=x/x.cummax().clip(lower=1.0)-1
    monthly=x.resample("ME").last().pct_change(); monthly.iloc[0]=x[x.index.to_period("M")==x.index[0].to_period("M")].iloc[-1]-1
    recent=x[x.index>=RECENT]; prior=x[x.index<RECENT]; base=prior.iloc[-1] if len(prior) else 1.0
    thirds=np.array_split(np.arange(len(x)),3); daily_ret=x.pct_change().fillna(x.iloc[0]-1).to_numpy()
    return {"return":float(x.iloc[-1]-1),"maximum_drawdown":float(-dd.min()),
            "recent12_return":float(recent.iloc[-1]/base-1) if len(recent) else 0.0,
            "positive_months":int((monthly>0).sum()),"negative_months":int((monthly<0).sum()),
            "worst_month":float(monthly.min()),"rebalance_sessions":rebalances,
            "minimum_cash":float(min_cash),"maximum_gross_to_equity":float(max_gross),
            "chronology":[float(np.prod(1+daily_ret[z])-1) for z in thirds]}


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    p,score,mask,_,_,_,_=base_context()
    if str(pd.Timestamp(p.dates.max()).date())!="2026-04-30" or int(p.readiness.get("holdout_rows_loaded_total",0))!=0:
        raise RuntimeError("holdout boundary failure")
    schedules,counts=build_schedules(p,score,mask)
    rows=[]
    for name,schedule in schedules.items():
        f=fixed_base(p,schedule); c=compounded(p,schedule)
        mode="control" if name=="control" else name.rsplit("_",1)[0]
        threshold=None if name=="control" else int(name.rsplit("_",1)[1])/100
        rows.append({"variant":name,"mode":mode,"threshold":threshold,**counts[name],
                     **{f"fixed_{k}":v for k,v in f.items()},**{f"compound_{k}":v for k,v in c.items()}})
    frame=pd.DataFrame(rows); frame.to_csv(OUT/"results.csv",index=False)
    report={"status":"completed","planned_variants":22,"executed_variants":len(rows),
            "maximum_loaded_date":"2026-04-30","holdout_rows_loaded":0,"results":rows}
    (OUT/"report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    cols=["variant","fixed_return","fixed_maximum_drawdown","fixed_recent12_return","fixed_positive_months","fixed_worst_month","fixed_turnover","blocked","substituted","cash_slots","compound_return","compound_maximum_drawdown","compound_recent12_return","compound_worst_month"]
    print(frame[cols].to_string(index=False))


if __name__=="__main__": main()
