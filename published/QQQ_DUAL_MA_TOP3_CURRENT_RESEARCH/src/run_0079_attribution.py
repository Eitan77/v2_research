from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];sys.path[:0]=[str(Path(__file__).parent),str(ROOT/'campaigns'/'CAM-0600'/'src')]
from run_0079_quote_fill import context
from suite_core import weekly_indices
OUT=ROOT/'campaigns'/'CAM-0611'/'artifacts'/'RUN-0079'
def main():
 series={}
 for s in [21,30]:
  x=pd.read_parquet(OUT/f'quote_daily_skip{s}_2bps.parquet');x.date=pd.to_datetime(x.date);series[s]=x.set_index('date').net_pnl
 z=pd.concat(series,axis=1).fillna(0);z.columns=['skip21','skip30'];z['difference']=z.skip30-z.skip21;delta=z.difference.sum();years=z.groupby(z.index.year).sum();months=z.groupby(z.index.to_period('M')).sum();weeks=z.groupby(z.index.to_period('W-FRI')).sum();top=z.nlargest(10,'difference')[['difference','skip21','skip30']]
 p,w=context();sig=weekly_indices(p.dates);sets={s:[set(np.flatnonzero(w[s][i]>0)) for i in sig] for s in [21,30]};overlap=[len(a&b) for a,b in zip(sets[21],sets[30])];same=[a==b for a,b in zip(sets[21],sets[30])]
 report={'total_improvement':delta,'early_improvement':float(z.loc[z.index<pd.Timestamp('2023-08-01'),'difference'].sum()),'late_improvement':float(z.loc[z.index>=pd.Timestamp('2023-08-01'),'difference'].sum()),'same_weekly_basket_fraction':float(np.mean(same)),'average_names_overlap_of_3':float(np.mean(overlap)),'different_signal_weeks':int(np.sum(~np.array(same))),'signal_weeks':len(sig),'positive_difference_weeks':int((weeks.difference>0).sum()),'negative_difference_weeks':int((weeks.difference<0).sum()),'top1_day_share_of_total_improvement':float(top.difference.iloc[0]/delta),'top5_days_share_of_total_improvement':float(top.difference.head().sum()/delta),'improvement_without_top1_day':float(delta-top.difference.iloc[0]),'improvement_without_top5_days':float(delta-top.difference.head().sum()),'yearly':years.reset_index().to_dict('records'),'largest_positive_difference_days':[{'date':str(i.date()),**r} for i,r in top.to_dict('index').items()],'months_skip30_better':int((months.difference>0).sum()),'months_skip21_better':int((months.difference<0).sum())};(OUT/'skip21_vs_30_attribution.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
