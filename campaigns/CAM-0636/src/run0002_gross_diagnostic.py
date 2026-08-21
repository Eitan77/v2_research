from __future__ import annotations

import json
from itertools import product

import pandas as pd

import run0001_bar_microtarget as base


def main() -> None:
    out = base.CAM / "artifacts" / "RUN-0002"; out.mkdir(parents=True, exist_ok=True)
    x = base.load(); sessions = pd.DatetimeIndex(sorted(x.date.unique())); rows=[]
    for green,rvol,target,hold,cost in product(base.GREEN_BP,base.RVOL,base.TARGET_BP,base.HOLDS,[0,1]):
        t=base.simulate(x,green,rvol,target,hold,cost); m=base.metrics(t,sessions)
        rows.append({"variant":f"g{green}_rv{rvol:g}_t{target}_h{hold}_c{cost}","green_bp":green,"rvol":rvol,"target_bp":target,"hold":hold,"cost_bp":cost,**{k:v for k,v in m.items() if k!='monthly'}})
    grid=pd.DataFrame(rows).sort_values(["cost_bp","net_return"],ascending=[True,False]); grid.to_csv(out/"grid.csv",index=False)
    report={"executed_variants":len(grid),"positive_by_cost":{},"best_by_cost":{}}
    for cost,g in grid.groupby("cost_bp"):
        report["positive_by_cost"][str(cost)]=int((g.net_return>0).sum())
        report["best_by_cost"][str(cost)]=g[g.trades>=30].head(5).to_dict("records")
    (out/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8"); print(json.dumps(report,indent=2))


if __name__ == "__main__": main()
