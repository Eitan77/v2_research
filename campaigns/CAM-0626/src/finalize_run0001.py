from pathlib import Path
import json,numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0001'
def metrics(d):
 eq=1+d.net_pnl.cumsum();pk=np.maximum.accumulate(np.r_[1.,eq])[1:];mo=d.set_index('date').net_pnl.resample('ME').sum();wk=d.set_index('date').net_pnl.resample('W-FRI').sum();return {'net_return':float(d.net_pnl.sum()),'max_drawdown':float(-(eq/pk-1).min()),'positive_days':int((d.net_pnl>0).sum()),'negative_days':int((d.net_pnl<0).sum()),'positive_weeks':int((wk>0).sum()),'negative_weeks':int((wk<0).sum()),'positive_months':int((mo>0).sum()),'negative_months':int((mo<0).sum()),'worst_day':float(d.net_pnl.min()),'worst_month':float(mo.min())}
d=pd.read_parquet(OUT/'daily_2bps.parquet');d.date=pd.to_datetime(d.date);t=pd.read_parquet(OUT/'trades_2bps.parquet');rows=[]
for b in (-1,0,1,2,5,10):
 x=d.copy();x['net_pnl']=x.gross_pnl-x.gross*2*b/10000;m=metrics(x);m.update({'bps_per_side':b,'trade_legs':len(t),'stops':int((t.exit_reason=='protective_stop').sum()),'long_gross':float(t[t.side.eq('long')].gross_pnl.sum()),'short_gross':float(t[t.side.eq('short')].gross_pnl.sum())});rows.append(m)
sel=pd.read_parquet(OUT/'selection_query.parquet');report={'status':'completed_bar_stage','planned_variants':6,'executed_variants':6,'selection_dates':int(sel.date.nunique()),'selection_rows':len(sel),'maximum_loaded_date':'2026-04-30','holdout_rows_loaded':0,'metrics':rows,'decision':'baseline_failed_gross_and_net_adapt_timing_selectivity'};(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(pd.DataFrame(rows).to_string(index=False))
