from pathlib import Path
import json,numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'campaigns'/'CAM-0626'/'artifacts'/'RUN-0003';KEY=['symbol','target_ts','role']
def load(pattern):
 fs=list(OUT.glob(pattern));parts=[pd.read_parquet(x) for x in fs if x.stat().st_size>0]
 if not parts:return pd.DataFrame(columns=KEY+['quote_ts','bid_price','ask_price'])
 q=pd.concat(parts,ignore_index=True);q.target_ts=pd.to_datetime(q.target_ts,utc=True);q.quote_ts=pd.to_datetime(q.quote_ts,utc=True)
 q['delay_seconds']=(q.quote_ts-q.target_ts).dt.total_seconds()
 # A quote arriving materially after the intended decision is not the requested fill.
 q=q[(q.delay_seconds>=0)&(q.delay_seconds<=60)]
 return q.sort_values(['delay_seconds','quote_ts']).drop_duplicates(KEY,keep='first')
def metrics(d):
 eq=1+d.net_pnl.cumsum();pk=np.maximum.accumulate(np.r_[1.,eq])[1:];mo=d.set_index('date').net_pnl.resample('ME').sum();wk=d.set_index('date').net_pnl.resample('W-FRI').sum();return {'net_return':float(d.net_pnl.sum()),'max_drawdown':float(-(eq/pk-1).min()),'positive_days':int((d.net_pnl>0).sum()),'negative_days':int((d.net_pnl<0).sum()),'positive_weeks':int((wk>0).sum()),'negative_weeks':int((wk<0).sum()),'positive_months':int((mo>0).sum()),'negative_months':int((mo<0).sum()),'worst_day':float(d.net_pnl.min()),'worst_month':float(mo.min())}
def main():
 l=pd.read_parquet(OUT/'quote_ledger_causal.parquet');l.target_ts=pd.to_datetime(l.target_ts,utc=True)
 qe=load('quotes_entry*.parquet');qx=load('quotes_exit_causal*.parquet');q=pd.concat([qe,qx],ignore_index=True).drop_duplicates(KEY)
 z=l.merge(q,on=KEY,how='left',validate='many_to_one',suffixes=('','_q'));z['fill']=np.where(z.side.eq('buy'),z.ask_price,z.bid_price)
 wide=z.pivot(index=['date','symbol','position_side','weight','bar_gross_pnl','exit_reason'],columns='phase',values='fill').reset_index()
 complete=wide.entry.notna()&wide.exit.notna();wide['quote_gross_pnl']=np.where(wide.position_side.eq('long'),wide.weight*(wide.exit/wide.entry-1),wide.weight*(1-wide.exit/wide.entry))
 days=pd.DataFrame({'date':pd.to_datetime(l.date.unique())}).sort_values('date');rows=[]
 for b in (0,1,2,5,10):
  t=wide[complete].copy();t['net_pnl']=t.quote_gross_pnl-t.weight*2*b/10000
  d=t.groupby('date').agg(net_pnl=('net_pnl','sum'),gross_pnl=('quote_gross_pnl','sum'),gross=('weight','sum'),legs=('symbol','size')).reset_index();d=days.merge(d,on='date',how='left').fillna(0)
  m=metrics(d);m.update({'additional_bps_per_side':b,'complete_trade_roles':int(complete.sum()),'total_trade_roles':len(wide),'trade_coverage':float(complete.mean()),'gross_return':float(d.gross_pnl.sum())});rows.append(m);d.to_parquet(OUT/f'quote_daily_{b}bps.parquet',index=False)
 wide.to_parquet(OUT/'quote_trades.parquet',index=False);missing=wide[~complete];missing.to_parquet(OUT/'missing_trades.parquet',index=False)
 report={'status':'completed' if complete.all() else 'incomplete_blocked','maximum_accepted_quote_delay_seconds':60,'entry_role_coverage':len(qe)/len(l[l.phase.eq('entry')]),'exit_role_coverage':len(qx)/len(l[l.phase.eq('exit')]),'complete_trade_coverage':float(complete.mean()),'missing_trades':len(missing),'maximum_loaded_date':str(pd.to_datetime(l.date).max().date()),'holdout_rows_loaded':0,'metrics':rows}
 (OUT/'quote_report.json').write_text(json.dumps(report,indent=2)+'\n');print(pd.DataFrame(rows).to_string(index=False));print({k:report[k] for k in ('status','entry_role_coverage','exit_role_coverage','complete_trade_coverage','missing_trades')})
if __name__=='__main__':main()
