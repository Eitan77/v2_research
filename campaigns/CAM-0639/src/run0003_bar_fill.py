from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd

ROOT=Path(__file__).resolve().parents[3];CAM=ROOT/'campaigns'/'CAM-0639';OUT=CAM/'artifacts'/'RUN-0003'

def calc(x,cost):
 y=x.copy();y['net_return']=y.gross_return-2*cost/1e4;daily=y.set_index('exit_date').net_return;monthly=daily.resample('ME').sum();yearly=daily.resample('YE').sum();eq=1+daily.cumsum();dd=(eq.cummax()-eq)/eq.cummax();comp=(1+y.net_return).prod()-1
 return {'additive_return':float(y.net_return.sum()),'compounded_return_diagnostic':float(comp),'mean_overnight_bp':float(y.net_return.mean()*1e4),'median_overnight_bp':float(y.net_return.median()*1e4),'win_rate':float((y.net_return>0).mean()),'max_drawdown':float(dd.max()),'positive_months':int((monthly>0).sum()),'negative_months':int((monthly<0).sum()),'positive_years':int((yearly>0).sum()),'negative_years':int((yearly<0).sum()),'monthly':{str(k.date()):float(v) for k,v in monthly.items()},'yearly':{str(k.year):float(v) for k,v in yearly.items()}}

def main():
 OUT.mkdir(parents=True,exist_ok=True);x=pd.read_parquet(CAM/'artifacts'/'RUN-0001'/'roles.parquet');x['entry_date']=pd.to_datetime(x.entry_date);x['exit_date']=pd.to_datetime(x.exit_date);x['gross_return']=x.exit_bar_open/x.entry_bar_close-1;x.to_csv(OUT/'trade_ledger.csv',index=False)
 costs={str(c):calc(x,c) for c in [0,1,2,5]};best=x.loc[x.gross_return.idxmax()];worst=x.loc[x.gross_return.idxmin()]
 report={'status':'complete','price_basis':'raw SIP one-minute bars; dividends excluded','start_entry':str(x.entry_date.min().date()),'final_exit':str(x.exit_date.max().date()),'overnights':len(x),'costs':costs,'best_overnight':{'entry_date':str(best.entry_date.date()),'exit_date':str(best.exit_date.date()),'return':float(best.gross_return)},'worst_overnight':{'entry_date':str(worst.entry_date.date()),'exit_date':str(worst.exit_date.date()),'return':float(worst.gross_return)}}
 (OUT/'report.json').write_text(json.dumps(report,indent=2));pd.DataFrame([{'cost_bp_side':k,**{z:v[z] for z in ['additive_return','compounded_return_diagnostic','mean_overnight_bp','win_rate','max_drawdown','positive_months','negative_months']} } for k,v in costs.items()]).to_csv(OUT/'cost_summary.csv',index=False);print(json.dumps({'overnights':len(x),'start':report['start_entry'],'end':report['final_exit'],'best_overnight':report['best_overnight'],'worst_overnight':report['worst_overnight'],'cost_summary':{k:{z:v[z] for z in ['additive_return','compounded_return_diagnostic','mean_overnight_bp','win_rate','max_drawdown','positive_months','negative_months','yearly']} for k,v in costs.items()}},indent=2))
if __name__=='__main__':main()
