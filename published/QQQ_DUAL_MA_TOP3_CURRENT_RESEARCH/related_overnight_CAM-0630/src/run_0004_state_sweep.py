from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0630'/'artifacts'/'RUN-0004'

def metric(z,mask,allocation):
 x=z[mask].copy();counts=x.groupby('exit_session').size()
 x['alloc']=1/3 if allocation=='cash_slots' else x.exit_session.map(1/counts)
 daily=(x.position_return*x.alloc).groupby(x.exit_session).sum();idx=pd.DatetimeIndex(sorted(z.exit_session.unique()));daily=daily.reindex(idx,fill_value=0);eq=1+daily.cumsum();dd=eq/eq.cummax().clip(lower=1)-1;mon=daily.groupby(daily.index.to_period('M')).sum();recent=daily[daily.index>=pd.Timestamp('2025-05-01')];rm=recent.groupby(recent.index.to_period('M')).sum();active=daily.ne(0);pos=daily.clip(lower=0).sort_values(ascending=False)
 return {'net_return':daily.sum(),'maximum_drawdown':-dd.min(),'worst_month':mon.min(),'positive_months':(mon>0).sum(),'negative_months':(mon<0).sum(),'active_sessions':active.sum(),'active_fraction':active.mean(),'positions':len(x),'average_qualifiers_when_active':counts.mean() if len(counts) else 0,'average_utilization':len(x)/(3*len(idx)) if allocation=='cash_slots' else active.mean(),'green_active_days':(daily[active]>0).sum(),'red_active_days':(daily[active]<0).sum(),'recent12_return':recent.sum(),'recent12_positive_months':(rm>0).sum(),'recent12_negative_months':(rm<0).sum(),'early_return':daily[daily.index<pd.Timestamp('2023-08-01')].sum(),'late_return':daily[daily.index>=pd.Timestamp('2023-08-01')].sum(),'top5_positive_day_share':pos.head(5).sum()/pos.sum() if pos.sum()>0 else np.nan}

def main():
 OUT.mkdir(parents=True,exist_ok=True);z=pd.read_parquet(ROOT/'campaigns'/'CAM-0630'/'artifacts'/'RUN-0003'/'positions_with_features.parquet');z.entry_session=pd.to_datetime(z.entry_session);z.exit_session=pd.to_datetime(z.exit_session);tests={'control':pd.Series(True,index=z.index)}
 for t in [-.01,-.02,-.03,-.04,-.05]:tests[f'prior_day_le_{t:g}']=z.prior_day_return<=t
 for t in [.25,.4,.5,.6,.75]:tests[f'dist200_ge_{t:g}']=z.distance_sma200>=t
 for t in [0,.01,.02,.03,.05]:tests[f'stock_day_ge_{t:g}']=z.stock_open_to_entry>=t
 tests.update({'qqq_up':z.qqq_open_to_entry>=0,'qqq_0_1':z.qqq_open_to_entry.between(0,.01),'qqq_0_2':z.qqq_open_to_entry.between(0,.02),'qqq_m1_p1':z.qqq_open_to_entry.between(-.01,.01)})
 for t in [10,20,40]:tests[f'age_ge_{t}']=z.age_sessions>=t
 tests.update({'prior5_le_m5':z.prior5_return<=-.05,'prior5_le_m10':z.prior5_return<=-.10,'prior5_ge_5':z.prior5_return>=.05})
 rows=[]
 for name,mask in tests.items():
  for allocation in ['cash_slots','full_port']:rows.append({'variant':name,'allocation':allocation,**metric(z,mask,allocation)})
 r=pd.DataFrame(rows);r.to_csv(OUT/'metrics.csv',index=False);report={'status':'completed','planned_variants':len(tests)*2,'executed_variants':len(r),'maximum_loaded_date':'2026-04-30','holdout_rows_loaded':0,'metrics':rows};(OUT/'report.json').write_text(json.dumps(report,indent=2,default=lambda x:x.item() if hasattr(x,'item') else x)+'\n');print(r.sort_values(['allocation','net_return'],ascending=[True,False]).to_string(index=False))
if __name__=='__main__':main()
