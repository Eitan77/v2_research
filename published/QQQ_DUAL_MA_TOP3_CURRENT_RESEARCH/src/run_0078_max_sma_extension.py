from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path[:0]=[str(Path(__file__).parent),str(ROOT/'campaigns'/'CAM-0600'/'src')]
from run_0033_exit_overlays import base_context
from run_0067_last_year_breadth import extension,signals
from run_0068_compounded_breadth import panel,simulate,START,END
from run_0077_two_entry_filters import metrics
OUT=ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0078'
def main():
 OUT.mkdir(parents=True,exist_ok=True);dates,rets,score,mask,_=panel();p,*_=base_context();_,_,ext_close,_,_,_=extension(p);cl=np.vstack([p.adj_close,ext_close]);sma=pd.DataFrame(cl,index=dates).rolling(200,min_periods=200).mean().to_numpy();rows=[];d,_=simulate(dates,rets,score,mask,3);rows.append(metrics(d,'baseline'))
 sig=[i for i in signals(dates) if START-pd.Timedelta(days=7)<=dates[i]<=END]
 for cap in [1.10,1.20,1.30,1.50,1.75]:
  f=mask&np.isfinite(sma)&(cl<=cap*sma);d,_=simulate(dates,rets,score,f,3);counts=[int((f[i]&np.isfinite(score[i])).sum()) for i in sig];rows.append(metrics(d,f'max_{round((cap-1)*100)}pct_above_sma200',{'median_eligible_names':float(np.median(counts)),'weeks_with_fewer_than_3':int(np.sum(np.array(counts)<3))}))
 x=pd.DataFrame(rows);x.to_csv(OUT/'comparison.csv',index=False);report={'status':'completed','window_start':str(START.date()),'window_end':str(END.date()),'maximum_loaded_date':str(dates.max().date()),'evidence_label':'observed_oos_descriptive_not_fresh_validation','metrics':rows};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=lambda v:v.item() if hasattr(v,'item') else v)+'\n');print(x.to_string(index=False))
if __name__=='__main__':main()
