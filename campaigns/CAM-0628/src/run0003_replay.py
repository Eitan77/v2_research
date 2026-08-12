from pathlib import Path
import json,numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0628'/'artifacts'/'RUN-0003';KEY=['symbol','target_ts','role']
def met(d):
 eq=1+d.pnl.cumsum();pk=np.maximum.accumulate(np.r_[1.,eq])[1:];mo=d.set_index('date').pnl.resample('ME').sum();wk=d.set_index('date').pnl.resample('W-FRI').sum();recent=d[d.date>=pd.Timestamp('2025-05-01')];pos=d.loc[d.pnl>0,'pnl'].sum();return {'net_return':float(d.pnl.sum()),'recent12_return':float(recent.pnl.sum()),'max_drawdown':float(-(eq/pk-1).min()),'positive_days':int((d.pnl>0).sum()),'negative_days':int((d.pnl<0).sum()),'positive_weeks':int((wk>0).sum()),'negative_weeks':int((wk<0).sum()),'positive_months':int((mo>0).sum()),'negative_months':int((mo<0).sum()),'worst_month':float(mo.min()),'top5_positive_day_share':float(d.nlargest(5,'pnl').pnl.sum()/pos) if pos>0 else 0.}
def main():
 roles=pd.read_parquet(OUT/'roles.parquet');roles.target_ts=pd.to_datetime(roles.target_ts,utc=True);q=pd.concat([pd.read_parquet(p) for p in sorted(OUT.glob('quotes*_60s.parquet'))],ignore_index=True);q.target_ts=pd.to_datetime(q.target_ts,utc=True);q.quote_ts=pd.to_datetime(q.quote_ts,utc=True);q['delay']=(q.quote_ts-q.target_ts).dt.total_seconds();q=q[(q.delay>=0)&(q.delay<=60)].sort_values(['delay','quote_ts']).drop_duplicates(KEY);z=roles.merge(q,on=KEY,how='left',validate='one_to_one');rows=[]
 for symbol in ('XLK','TQQQ','SOXL'):
  for clock in ('0930','0940'):
   e=z[(z.symbol.eq(symbol))&(z.role.eq('entry'+clock))][['date','weight','ask_price']].rename(columns={'ask_price':'entry'});x=z[(z.symbol.eq(symbol))&(z.role.eq('exit1550'))][['date','bid_price']].rename(columns={'bid_price':'exit'});d=e.merge(x,on='date',how='left');coverage=float(d[['entry','exit']].notna().all(axis=1).mean());d=d[d[['entry','exit']].notna().all(axis=1)].copy();d.date=pd.to_datetime(d.date)
   for b in (-1,0,1,2,5,10):
    k=b/10000;d['pnl']=d.weight*(d.exit*(1-k)/(d.entry*(1+k))-1);m=met(d);m.update({'symbol':symbol,'entry_clock':clock,'additional_bps_per_side':b,'package_coverage':coverage,'executed_days':len(d)});rows.append(m)
 pd.DataFrame(rows).to_parquet(OUT/'metrics.parquet',index=False);report={'status':'completed','metrics':rows,'quote_role_coverage':float(z.bid_price.notna().mean()),'maximum_loaded_date':str(pd.to_datetime(roles.date).max().date()),'holdout_rows_loaded':0};(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(pd.DataFrame(rows).sort_values(['additional_bps_per_side','net_return'],ascending=[True,False]).to_string(index=False))
if __name__=='__main__':main()
