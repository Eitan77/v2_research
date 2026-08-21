from __future__ import annotations

import json
import pandas as pd

import run0006_symmetric_oco as oco


OUT=oco.replay.base.CAM/"artifacts"/"RUN-0007"


def main():
    OUT.mkdir(parents=True,exist_ok=True); sig,q=oco.inputs(); rows=[]
    for target in [5,10,15,20,30]:
      for hold in [1,3,5]:
        x=oco.run(sig,q,target,hold,1); m=oco.summary(x)
        rows.append({"target_bp":target,"hold_min":hold,**m}); x.assign(target_bp=target,hold_min=hold).to_csv(OUT/f"ledger_t{target}_h{hold}.csv",index=False)
    grid=pd.DataFrame(rows).sort_values("net_return",ascending=False); grid.to_csv(OUT/"grid.csv",index=False)
    report={"cells":len(grid),"profitable_cells":int((grid.net_return>0).sum()),"best":grid.head(10).to_dict("records")}
    (OUT/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8"); print(json.dumps(report,indent=2))


if __name__=="__main__":main()
